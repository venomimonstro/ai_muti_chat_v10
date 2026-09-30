from __future__ import annotations

import hashlib
import os
import sys
import threading
from contextlib import contextmanager

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection

from .models import Generation, Message

LOCK_SECONDS = max(15, int(os.getenv("CHAT_PREPARE_LOCK_SECONDS", "180")))
ACTIVE_STATES = {Generation.State.QUEUED, Generation.State.RUNNING}
_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


def _lock_key(conversation_id) -> str:
    return f"chat:prepare:{conversation_id}"


def _existing_replay(*, user, conversation, client_message_id, idempotency_key):
    if Generation.objects.filter(owner=user, idempotency_key=idempotency_key).exists():
        return True
    return Message.objects.filter(
        conversation=conversation,
        client_message_id=client_message_id,
        role=Message.Role.USER,
        generation_request__isnull=False,
    ).exists()


def _active_generation_exists(conversation) -> bool:
    return Generation.objects.filter(
        user_message__conversation=conversation,
        state__in=ACTIVE_STATES,
    ).exists()


def _advisory_key(conversation_id) -> int:
    raw = hashlib.sha256(f"chat-single-flight:{conversation_id}".encode("utf-8")).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


@contextmanager
def _database_fallback_lock(conversation_id):
    """Serialize one conversation when Redis/cache is unavailable.

    PostgreSQL advisory locks are connection-scoped and automatically disappear if
    the process/connection dies. They do not lock unrelated conversations. SQLite
    and other non-production engines use a process-local lock for deterministic
    tests/development only.
    """
    if connection.vendor == "postgresql":
        key = _advisory_key(conversation_id)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [key])
        try:
            yield
        finally:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
            except Exception:
                # A dropped DB connection releases session advisory locks itself.
                pass
        return

    name = str(conversation_id)
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.setdefault(name, threading.Lock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _cache_lock_acquire(key: str):
    try:
        return bool(cache.add(key, "1", timeout=LOCK_SECONDS))
    except Exception:
        return None


def _cache_lock_release(key: str) -> None:
    try:
        cache.delete(key)
    except Exception:
        pass


def install(streaming_module) -> None:
    """Serialize creation of new generations for one conversation.

    Idempotent replays remain legal. Redis is the fast path, but never a hard
    dependency of the core chat: a PostgreSQL advisory lock preserves single-flight
    semantics during cache outages without double provider calls or double billing.
    """
    raw_prepare = streaming_module.prepare
    if getattr(raw_prepare, "_ai_workspace_single_flight", False):
        return

    def execute_after_lock(*, user, conversation, content, client_message_id, idempotency_key, file_ids=None):
        if _existing_replay(
            user=user,
            conversation=conversation,
            client_message_id=client_message_id,
            idempotency_key=idempotency_key,
        ):
            return raw_prepare(
                user=user,
                conversation=conversation,
                content=content,
                client_message_id=client_message_id,
                idempotency_key=idempotency_key,
                file_ids=file_ids,
            )
        if _active_generation_exists(conversation):
            raise ValidationError(
                "Предыдущий ответ ещё формируется. Дождитесь завершения или остановите его перед новым сообщением."
            )
        return raw_prepare(
            user=user,
            conversation=conversation,
            content=content,
            client_message_id=client_message_id,
            idempotency_key=idempotency_key,
            file_ids=file_ids,
        )

    def prepare(*, user, conversation, content, client_message_id, idempotency_key, file_ids=None):
        if _existing_replay(
            user=user,
            conversation=conversation,
            client_message_id=client_message_id,
            idempotency_key=idempotency_key,
        ):
            return raw_prepare(
                user=user,
                conversation=conversation,
                content=content,
                client_message_id=client_message_id,
                idempotency_key=idempotency_key,
                file_ids=file_ids,
            )
        if _active_generation_exists(conversation):
            raise ValidationError(
                "Предыдущий ответ ещё формируется. Дождитесь завершения или остановите его перед новым сообщением."
            )

        key = _lock_key(conversation.id)
        acquired = _cache_lock_acquire(key)
        if acquired is False:
            raise ValidationError(
                "Предыдущий запрос ещё принимается. Повторите отправку после его подтверждения."
            )
        if acquired is None:
            with _database_fallback_lock(conversation.id):
                return execute_after_lock(
                    user=user,
                    conversation=conversation,
                    content=content,
                    client_message_id=client_message_id,
                    idempotency_key=idempotency_key,
                    file_ids=file_ids,
                )
        try:
            return execute_after_lock(
                user=user,
                conversation=conversation,
                content=content,
                client_message_id=client_message_id,
                idempotency_key=idempotency_key,
                file_ids=file_ids,
            )
        finally:
            _cache_lock_release(key)

    prepare._ai_workspace_single_flight = True
    prepare._raw_prepare = raw_prepare
    streaming_module.prepare = prepare

    # Rebind modules that may have imported prepare by value before AppConfig.ready().
    for module_name in ("apps.chat.services", "apps.chat.views"):
        module = sys.modules.get(module_name)
        if module is not None:
            module.prepare = prepare
