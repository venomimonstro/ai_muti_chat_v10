import asyncio
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation

from django.db import close_old_connections

from .managed_stream import _finalize_unhandled_failure, managed_run
from .models import Generation
from .pipeline_trace import trace as pipeline_trace
from .streaming import sse

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_SECONDS = 10.0
FOLLOW_POLL_SECONDS = 1.0
STREAM_EXECUTOR_WORKERS = max(
    4,
    min(int(os.getenv("CHAT_STREAM_EXECUTOR_WORKERS", "16")), 128),
)
STREAM_MAX_INFLIGHT = max(
    STREAM_EXECUTOR_WORKERS,
    min(int(os.getenv("CHAT_STREAM_MAX_INFLIGHT", str(STREAM_EXECUTOR_WORKERS * 4))), 512),
)
_STREAM_EXECUTOR = ThreadPoolExecutor(
    max_workers=STREAM_EXECUTOR_WORKERS,
    thread_name_prefix="chat-stream",
)
_STREAM_SLOTS = threading.BoundedSemaphore(STREAM_MAX_INFLIGHT)

_PROVIDER_ERROR_MARKERS = (
    "provider_",
    "gigachat_",
    "deepseek_",
    "openai_",
    "anthropic_",
    "openrouter_",
    "gemini_",
    "xai_",
)
_PROVIDER_ERROR_CODES = {
    "timeout",
    "invalid_stream",
    "network_error",
    "rate_limited",
    "authentication_error",
    "permission_denied",
    "model_not_found",
    "invalid_api_key",
    "credential_missing",
    "credit_balance_exhausted",
    "insufficient_quota",
    "organization_usage_limit_exceeded",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
}
_PUBLIC_ERROR_CODES = {
    "AI-102",
    "AI-103",
    "generation_in_progress",
    "partial_response_interrupted",
}


def _is_provider_error(code):
    value = str(code or "").casefold()
    return value in _PROVIDER_ERROR_CODES or any(marker in value for marker in _PROVIDER_ERROR_MARKERS)


def _generation_in_progress_chunk(chunk) -> bool:
    if not isinstance(chunk, str) or not chunk.startswith("event: error"):
        return False
    try:
        data_line = next(line for line in chunk.splitlines() if line.startswith("data:"))
        payload = json.loads(data_line[5:].strip())
    except Exception:
        return False
    return str((payload or {}).get("code") or "") == "generation_in_progress"


def _positive_cost(value) -> bool:
    try:
        return Decimal(str(value or "0")) > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def _internal_failure_message(payload):
    if _positive_cost(payload.get("cost_rub")):
        return (
            f"Ответ прервался после подтверждённого расхода AI. Списана только подтверждённая "
            f"стоимость {payload['cost_rub']} ₽; остаток резерва возвращён. Ответ сохранён — "
            "можно повторить запрос."
        )
    return (
        "Не удалось завершить ответ из-за внутреннего сбоя соединения. Запрос сохранён, "
        "неподтверждённые расходы не списаны. Повторите запрос."
    )


def _public_chunk(chunk):
    """Hide provider/key/runtime internals without contradicting authoritative billing."""
    if not isinstance(chunk, str) or not chunk.startswith("event: error\n"):
        return chunk
    try:
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line[6:])
    except Exception:
        return chunk
    code = str(payload.get("code") or "")
    payload.pop("cause_code", None)
    if code in {"generation_in_progress", "partial_response_interrupted", "AI-102", "AI-103"}:
        return f"event: error\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
    if _is_provider_error(code):
        payload["code"] = "AI-102"
        payload["support_code"] = "AI-102"
        if _positive_cost(payload.get("cost_rub")):
            payload["message"] = (
                f"Ответ прервался после подтверждённого расхода AI. Списана только подтверждённая "
                f"стоимость {payload['cost_rub']} ₽; остаток резерва возвращён. Повторите запрос — "
                "система автоматически выберет доступный AI-канал."
            )
        else:
            payload["message"] = (
                "Сервис временно не смог завершить ответ. Деньги за незавершённый запрос не списаны. "
                "Повторите запрос — система автоматически выберет доступный AI-канал."
            )
        return f"event: error\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

    payload["code"] = "AI-103"
    payload["support_code"] = "AI-103"
    payload["message"] = _internal_failure_message(payload)
    return f"event: error\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _enqueue(loop, queue, item, detached):
    if detached.is_set():
        return False
    try:
        loop.call_soon_threadsafe(queue.put_nowait, item)
        return True
    except RuntimeError:
        detached.set()
        return False


def _produce(generation, loop, queue, detached):
    close_old_connections()
    stream = managed_run(generation)
    try:
        for chunk in stream:
            if detached.is_set():
                continue
            _enqueue(loop, queue, ("chunk", chunk), detached)
    except BaseException as exc:  # pragma: no cover - defensive last-resort guard
        logger.exception("ASGI chat stream worker crashed generation_id=%s", generation.id)
        if not detached.is_set():
            _enqueue(loop, queue, ("error", exc), detached)
    finally:
        close_old_connections()
        if not detached.is_set():
            _enqueue(loop, queue, ("done", None), detached)


def _produce_with_slot(generation, loop, queue, detached):
    try:
        _produce(generation, loop, queue, detached)
    finally:
        _STREAM_SLOTS.release()


def _generation_snapshot(generation_id):
    close_old_connections()
    try:
        row = (
            Generation.objects.select_related("assistant_message")
            .only(
                "id",
                "state",
                "error_code",
                "actual_cost_rub",
                "assistant_message__content",
                "assistant_message__status",
            )
            .get(pk=generation_id)
        )
        return {
            "state": row.state,
            "error_code": row.error_code,
            "cost_rub": str(row.actual_cost_rub or 0),
            "text": row.assistant_message.content,
            "message_status": row.assistant_message.status,
        }
    finally:
        close_old_connections()


def _public_failed_snapshot(snapshot):
    internal_code = str(snapshot.get("error_code") or "")
    if _is_provider_error(internal_code):
        public_code = "AI-102"
    elif internal_code in _PUBLIC_ERROR_CODES:
        public_code = internal_code
    else:
        public_code = "AI-103"
    charged = _positive_cost(snapshot.get("cost_rub"))
    if charged:
        message = (
            f"Запрос прервался после подтверждённого расхода AI. Списана только подтверждённая "
            f"стоимость {snapshot['cost_rub']} ₽; остаток резерва возвращён."
        )
    elif public_code == "AI-102":
        message = (
            "Сервис временно не смог завершить ответ. Деньги за незавершённый запрос не списаны. "
            "Повторите запрос."
        )
    else:
        message = (
            "Запрос завершился с внутренней ошибкой после восстановления соединения. "
            "Неподтверждённые расходы не списаны. Повторите запрос."
        )
    return public_code, message


async def follow_generation_async(
    generation,
    *,
    heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS,
    poll_seconds=FOLLOW_POLL_SECONDS,
):
    yield sse("generation", {"id": str(generation.id), "state": generation.state, "reconnected": True})
    last_text = None
    heartbeat_deadline = asyncio.get_running_loop().time()
    while True:
        snapshot = await asyncio.to_thread(_generation_snapshot, generation.id)
        state = snapshot["state"]
        if state == Generation.State.COMPLETED:
            yield sse(
                "snapshot",
                {
                    "text": snapshot["text"],
                    "state": state,
                    "cost_rub": snapshot["cost_rub"],
                    "reconnected": True,
                },
            )
            return
        if state == Generation.State.CANCELLED:
            if snapshot["text"]:
                yield sse(
                    "snapshot",
                    {
                        "text": snapshot["text"],
                        "state": state,
                        "cost_rub": snapshot["cost_rub"],
                        "reconnected": True,
                    },
                )
            yield sse(
                "cancelled",
                {
                    "code": "client_cancelled",
                    "state": state,
                    "partial": bool(snapshot["text"]),
                    "cost_rub": snapshot["cost_rub"],
                    "reconnected": True,
                    "message": (
                        "Генерация остановлена. Списана только подтверждённая стоимость уже полученной части ответа."
                        if _positive_cost(snapshot["cost_rub"])
                        else "Запрос остановлен пользователем. Неподтверждённые расходы не списаны."
                    ),
                },
            )
            return
        if state == Generation.State.FAILED:
            if snapshot["text"]:
                yield sse(
                    "snapshot",
                    {
                        "text": snapshot["text"],
                        "state": state,
                        "cost_rub": snapshot["cost_rub"],
                        "reconnected": True,
                    },
                )
            public_code, message = _public_failed_snapshot(snapshot)
            yield sse(
                "error",
                {
                    "code": public_code,
                    "support_code": public_code,
                    "partial": bool(snapshot["text"]),
                    "cost_rub": snapshot["cost_rub"],
                    "message": message,
                },
            )
            return

        if snapshot["text"] and snapshot["text"] != last_text:
            last_text = snapshot["text"]
            yield sse("snapshot", {"text": last_text, "state": state, "cost_rub": snapshot["cost_rub"], "reconnected": True})
        now = asyncio.get_running_loop().time()
        if now >= heartbeat_deadline:
            yield sse(
                "heartbeat",
                {
                    "generation_id": str(generation.id),
                    "state": state,
                    "reconnected": True,
                },
            )
            heartbeat_deadline = now + max(1.0, float(heartbeat_seconds))
        await asyncio.sleep(max(0.2, float(poll_seconds)))


async def _finalize_async(generation):
    await asyncio.to_thread(_finalize_unhandled_failure, generation)


async def managed_run_async(generation, *, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS):
    """Native ASGI iterator with bounded provider execution and durable reconnect.

    The durable turn is acknowledged immediately, before it waits for an executor
    thread. This prevents browser first-event timeouts from opening additional
    producer attempts for the same still-QUEUED Generation under load.
    """
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    detached = threading.Event()

    pipeline_trace("SSE_ACCEPTED", generation=generation)
    yield sse(
        "generation",
        {
            "id": str(generation.id),
            "state": getattr(generation, "state", "queued"),
            "accepted": True,
        },
    )

    if not _STREAM_SLOTS.acquire(blocking=False):
        await _finalize_async(generation)
        yield sse(
            "error",
            {
                "code": "AI-103",
                "support_code": "AI-103",
                "message": (
                    "Сервис обрабатывает максимальное число AI-запросов. Текущий запрос безопасно "
                    "остановлен, неподтверждённые расходы не списаны. Повторите через несколько секунд."
                ),
            },
        )
        return

    try:
        try:
            _STREAM_EXECUTOR.submit(_produce_with_slot, generation, loop, queue, detached)
        except RuntimeError:
            _STREAM_SLOTS.release()
            await _finalize_async(generation)
            yield sse(
                "error",
                {
                    "code": "AI-103",
                    "support_code": "AI-103",
                    "message": "Не удалось запустить обработку ответа. Запрос сохранён, неподтверждённые расходы не списаны.",
                },
            )
            return

        while True:
            try:
                kind, value = await asyncio.wait_for(
                    queue.get(), timeout=max(0.05, float(heartbeat_seconds))
                )
            except TimeoutError:
                try:
                    snapshot = await asyncio.to_thread(_generation_snapshot, generation.id)
                    heartbeat_state = snapshot["state"]
                except Exception:
                    heartbeat_state = "running"
                yield sse(
                    "heartbeat",
                    {"generation_id": str(generation.id), "state": heartbeat_state},
                )
                continue

            if kind == "chunk":
                if isinstance(value, str) and value.startswith("event: "):
                    event_name = value.splitlines()[0][7:].strip()
                    if event_name in {"completed", "error", "cancelled", "snapshot"}:
                        pipeline_trace(
                            "SSE_CHUNK_READY",
                            generation=generation,
                            event=event_name,
                        )
                # streaming.run() emits its own generation event after acquiring
                # the durable RUNNING claim. The ASGI transport already acknowledged
                # the same Generation before queueing, so suppress only that duplicate.
                if isinstance(value, str) and value.startswith("event: generation\n"):
                    continue
                if _generation_in_progress_chunk(value):
                    # A simultaneous reconnect lost the atomic QUEUED -> RUNNING
                    # producer claim. It is not a customer-visible failure: attach to
                    # the winning producer and replay durable snapshots/terminal state.
                    detached.set()
                    async for follow_chunk in follow_generation_async(generation):
                        if isinstance(follow_chunk, str) and follow_chunk.startswith(
                            "event: generation\n"
                        ):
                            continue
                        yield follow_chunk
                    return
                yield _public_chunk(value)
                continue
            if kind == "error":
                pipeline_trace(
                    "SSE_WORKER_ERROR",
                    generation=generation,
                    error_type=type(value).__name__,
                    error=str(value)[:500],
                )
                yield sse(
                    "error",
                    {
                        "code": "AI-103",
                        "support_code": "AI-103",
                        "message": "Соединение с AI временно прервалось. Ответ сохранён; выполняется безопасное восстановление.",
                    },
                )
                return
            if kind == "done":
                pipeline_trace("SSE_DONE", generation=generation)
                return
    except (asyncio.CancelledError, GeneratorExit):
        detached.set()
        raise
    finally:
        detached.set()
