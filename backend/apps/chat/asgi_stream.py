import asyncio
import logging
import threading

from django.db import close_old_connections

from .managed_stream import managed_run
from .streaming import sse

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_SECONDS = 10.0


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
                yield value
                continue
            if kind == "error":
                # managed_run converts normal provider/runtime failures to public
                # SSE errors. This branch covers only a bridge-level crash.
                yield sse(
                    "error",
                    {
                        "code": "stream_bridge_error",
                        "message": "Соединение с AI временно прервалось. Ответ сохранён; выполняется безопасное восстановление.",
                    },
                )
                return
            if kind == "done":
                return
    except (asyncio.CancelledError, GeneratorExit):
        # Never close the synchronous provider generator on transport loss. The
        # worker persists the final answer and billing state for safe reconnect.
        detached.set()
        raise
    finally:
        detached.set()
