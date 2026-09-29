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
        future = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
        future.result(timeout=30)
        return True
    except Exception:
        detached.set()
        return False


def _produce(generation, loop, queue, detached):
    """Consume the existing synchronous provider stream outside the ASGI loop.

    A browser disconnect must not close managed_run(): the paid/provider request may
    already be in flight. We keep consuming it to a durable Generation state, while
    simply detaching delivery to the vanished client. A reconnect can then recover
    the completed snapshot using the same idempotency key.
    """
    close_old_connections()
    stream = managed_run(generation)
    try:
        for chunk in stream:
            if detached.is_set():
                continue
            if not _enqueue(loop, queue, ("chunk", chunk), detached):
                continue
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
    """Native ASGI iterator with heartbeat and durable disconnect behaviour."""
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=128)
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
                # managed_run already converts normal provider/runtime failures to
                # public SSE errors. This branch only covers an unexpected bridge
                # failure, without exposing an internal exception to the client.
                yield sse(
                    "error",
                    {
                        "code": "stream_bridge_error",
                        "message": "Соединение с AI временно прервалось. Ответ сохранён; повторное подключение выполняется безопасно.",
                    },
                )
                return
            if kind == "done":
                return
    except (asyncio.CancelledError, GeneratorExit):
        # Do not close the synchronous provider generator here. It continues in
        # the worker and persists the final answer/billing state. This makes a
        # transient browser/proxy disconnect recoverable instead of cancelling a
        # successful provider request halfway through.
        detached.set()
        raise
    finally:
        detached.set()
