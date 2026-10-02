from __future__ import annotations

import os
import time
from concurrent.futures import TimeoutError as FutureTimeoutError


MAX_PENDING_EVENTS = max(
    32,
    min(int(os.getenv("CHAT_STREAM_MAX_PENDING_EVENTS", "256")), 4096),
)
WAIT_SLICE_SECONDS = 0.05
FUTURE_WAIT_SECONDS = 0.5


def install(asgi_stream_module) -> None:
    """Apply bounded backpressure between provider threads and slow SSE clients.

    ``asyncio.Queue`` is intentionally left unbounded at the object level because
    replacing asyncio primitives globally is unsafe. Instead the only producer for a
    generation synchronously hands each item to the event loop and pauses once the
    per-stream queue reaches a bounded backlog. A slow/mobile client therefore cannot
    turn a fast provider stream into unbounded process memory growth.

    Disconnect remains non-destructive: ASGI sets ``detached`` and this producer stops
    queueing immediately, while the provider worker continues the durable Generation
    to completion so a reconnect can read the saved answer.
    """
    raw_enqueue = asgi_stream_module._enqueue
    if getattr(raw_enqueue, "_ai_workspace_backpressure", False) is True:
        return

    def enqueue(loop, queue, item, detached):
        if detached.is_set():
            return False

        while queue.qsize() >= MAX_PENDING_EVENTS:
            if detached.is_set():
                return False
            time.sleep(WAIT_SLICE_SECONDS)

        try:
            future = asgi_stream_module.asyncio.run_coroutine_threadsafe(
                queue.put(item),
                loop,
            )
        except RuntimeError:
            detached.set()
            return False

        while True:
            if detached.is_set():
                future.cancel()
                return False
            try:
                future.result(timeout=FUTURE_WAIT_SECONDS)
                return True
            except FutureTimeoutError:
                continue
            except Exception:
                detached.set()
                return False

    enqueue._ai_workspace_backpressure = True
    enqueue._raw_enqueue = raw_enqueue
    asgi_stream_module._enqueue = enqueue
