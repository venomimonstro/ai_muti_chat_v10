from __future__ import annotations

import asyncio
import logging
import sys
import threading

from django.contrib.auth import get_user_model
from django.db import close_old_connections

from .activity_stream import managed_request_stream as _sync_managed_request_stream
from .asgi_stream import (
    DEFAULT_HEARTBEAT_SECONDS,
    _STREAM_EXECUTOR,
    _STREAM_SLOTS,
    _enqueue,
    _public_chunk,
)
from .models import Conversation
from .streaming import sse

logger = logging.getLogger(__name__)


def _produce_request(*, user_id, conversation_id, idempotency_key, payload, loop, queue, detached):
    """Run the authoritative sync chat pipeline outside the ASGI event loop.

    Business/routing/billing behavior stays in ``activity_stream``. This adapter
    only moves that blocking iterator to the bounded chat executor and forwards
    each SSE chunk to the native async response as soon as it is produced.
    """
    close_old_connections()
    try:
        User = get_user_model()
        user = User.objects.get(pk=user_id)
        conversation = Conversation.objects.get(pk=conversation_id, owner_id=user_id)
        stream = _sync_managed_request_stream(
            user=user,
            conversation=conversation,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        for chunk in stream:
            if detached.is_set():
                # A network disconnect must not kill an already accepted generation.
                # Explicit Stop uses the durable cancellation endpoint instead.
                continue
            _enqueue(loop, queue, ("chunk", chunk), detached)
    except BaseException as exc:  # pragma: no cover - defensive transport guard
        logger.exception(
            "ASGI request stream worker crashed owner_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )
        if not detached.is_set():
            _enqueue(loop, queue, ("error", exc), detached)
    finally:
        close_old_connections()
        if not detached.is_set():
            _enqueue(loop, queue, ("done", None), detached)


def _produce_request_with_slot(**kwargs):
    try:
        _produce_request(**kwargs)
    finally:
        _STREAM_SLOTS.release()


async def managed_request_stream_async(
    *,
    user,
    conversation,
    idempotency_key: str,
    payload: dict,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
):
    """Native-ASGI bridge for the complete customer chat request.

    Django 5 under Uvicorn must receive an asynchronous streaming iterator. Passing
    a synchronous generator directly to ``StreamingHttpResponse`` forces sync/async
    adaptation and can defeat incremental SSE delivery. The bounded executor also
    prevents an unbounded number of provider/preflight threads.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    detached = threading.Event()

    if not _STREAM_SLOTS.acquire(blocking=False):
        yield sse(
            "error",
            {
                "code": "AI-103",
                "support_code": "AI-103",
                "message": (
                    "Сервис сейчас обрабатывает максимальное число AI-запросов. "
                    "Деньги не списаны. Повторите через несколько секунд."
                ),
            },
        )
        return

    try:
        try:
            _STREAM_EXECUTOR.submit(
                _produce_request_with_slot,
                user_id=user.pk,
                conversation_id=conversation.pk,
                idempotency_key=idempotency_key,
                payload=dict(payload),
                loop=loop,
                queue=queue,
                detached=detached,
            )
        except RuntimeError:
            _STREAM_SLOTS.release()
            yield sse(
                "error",
                {
                    "code": "AI-103",
                    "support_code": "AI-103",
                    "message": (
                        "Не удалось запустить обработку сообщения. Деньги не списаны. "
                        "Повторите запрос."
                    ),
                },
            )
            return

        while True:
            try:
                kind, value = await asyncio.wait_for(
                    queue.get(), timeout=max(1.0, float(heartbeat_seconds))
                )
            except asyncio.TimeoutError:
                # Keep nginx/browser/proxy chains alive while search/provider work is
                # legitimately taking longer than the visible activity interval.
                yield sse("heartbeat", {"state": "working"})
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
                        "message": (
                            "Внутренний поток ответа временно прервался. Запрос сохранён, "
                            "неподтверждённые расходы не списаны. Повторите запрос."
                        ),
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


def install(activity_stream_module) -> None:
    """Make the public DRF endpoint emit a native async StreamingHttpResponse."""
    if getattr(activity_stream_module.managed_request_stream, "_ai_workspace_native_asgi", False):
        return
    managed_request_stream_async._ai_workspace_native_asgi = True
    managed_request_stream_async._sync_pipeline = _sync_managed_request_stream
    activity_stream_module.managed_request_stream = managed_request_stream_async

    # URL modules can be imported before or after AppConfig.ready(). Rebind the
    # already-loaded view module now; future imports receive the patched symbol from
    # activity_stream directly.
    views_module = sys.modules.get("apps.chat.views")
    if views_module is not None:
        views_module.managed_request_stream = managed_request_stream_async
