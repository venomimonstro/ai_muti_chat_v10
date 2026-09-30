import asyncio
import json
import logging
import threading
from decimal import Decimal, InvalidOperation

from django.db import close_old_connections

from .managed_stream import managed_run
from .models import Generation
from .streaming import sse

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_SECONDS = 10.0
FOLLOW_POLL_SECONDS = 1.0

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


def _is_provider_error(code):
    value = str(code or "").casefold()
    return value in _PROVIDER_ERROR_CODES or any(marker in value for marker in _PROVIDER_ERROR_MARKERS)


def _positive_cost(value) -> bool:
    try:
        return Decimal(str(value or "0")) > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def _public_chunk(chunk):
    """Hide provider/key/quota internals without contradicting authoritative billing."""
    if not isinstance(chunk, str) or not chunk.startswith("event: error\n"):
        return chunk
    try:
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        payload = json.loads(data_line[6:])
    except Exception:
        return chunk
    code = str(payload.get("code") or "")
    if code == "generation_in_progress":
        return chunk
    if _is_provider_error(code):
        payload["code"] = "AI-102"
        payload["support_code"] = "AI-102"
        if _positive_cost(payload.get("cost_rub")):
            # managed_stream has already reconciled confirmed provider usage. Do not
            # overwrite that truth with the generic no-charge transport message.
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
    return chunk


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
    """Consume the existing synchronous provider stream outside the ASGI loop.

    A browser disconnect must not close managed_run(): the provider request may
    already be in flight. We keep consuming it to a durable Generation state and
    only detach delivery to the vanished client. Reconnect then recovers the same
    generation through its idempotency key.
    """
    close_old_connections()
    stream = managed_run(generation)
    try:
        for chunk in stream:
            if detached.is_set():
                continue
            _enqueue(loop, queue, ("chunk", chunk), detached)
    except BaseException as exc:  # pragma: no cover - defensive last-resort guard
        logger.exception(
            "ASGI chat stream worker crashed generation_id=%s",
            generation.id,
        )
        if not detached.is_set():
            _enqueue(loop, queue, ("error", exc), detached)
    finally:
        close_old_connections()
        if not detached.is_set():
            _enqueue(loop, queue, ("done", None), detached)


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


async def follow_generation_async(
    generation,
    *,
    heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS,
    poll_seconds=FOLLOW_POLL_SECONDS,
):
    """Follow an already-running idempotent generation after transport reconnect.

    The original provider request remains the single writer. This follower only
    observes durable DB state, so reconnects cannot double-call a provider or
    reserve/charge the user twice.
    """
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
                    "code": snapshot["error_code"] or "client_cancelled",
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
            public_code = "AI-102" if _is_provider_error(snapshot["error_code"]) else (snapshot["error_code"] or "generation_failed")
            charged = _positive_cost(snapshot["cost_rub"])
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
                message = "Запрос завершился с ошибкой после восстановления соединения. Деньги без подтверждённого расхода не списаны."
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


async def managed_run_async(generation, *, heartbeat_seconds=DEFAULT_HEARTBEAT_SECONDS):
    """Native ASGI iterator with heartbeat and durable disconnect behaviour.

    The queue is intentionally unbounded: one generation is capped by the model
    output-token limit, while avoiding a producer deadlock when a browser/proxy
    disappears between provider chunks.
    """
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    detached = threading.Event()
    worker = threading.Thread(
        target=_produce,
        args=(generation, loop, queue, detached),
        name=f"chat-stream-{generation.id}",
        daemon=True,
    )
    worker.start()

    try:
        while True:
            try:
                kind, value = await asyncio.wait_for(
                    queue.get(), timeout=max(0.05, float(heartbeat_seconds))
                )
            except asyncio.TimeoutError:
                yield sse(
                    "heartbeat",
                    {"generation_id": str(generation.id), "state": "running"},
                )
                continue

            if kind == "chunk":
                yield _public_chunk(value)
                continue
            if kind == "error":
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
                return
    except (asyncio.CancelledError, GeneratorExit):
        detached.set()
        raise
    finally:
        detached.set()
