from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.db import connection

from .models import Generation, Message

ACTIVE_STATES = {Generation.State.QUEUED, Generation.State.RUNNING}
LOCK_WAIT_SECONDS = max(1.0, min(float(os.getenv("CHAT_SINGLE_FLIGHT_WAIT_SECONDS", "8")), 30.0))
LOCK_POLL_SECONDS = 0.05
_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


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


def _recover_stale_active_generations(conversation) -> int:
    """Opportunistically close already-stale work before returning a busy error.

    The scheduled recovery task remains the normal self-healing path. This fast path
    only considers generations that have *already* crossed the same authoritative
    stale cutoff and delegates every safety/billing decision to admin_ops.recovery.
    A fresh RUNNING GenerationAttempt is therefore preserved by the existing recovery
    guard and a healthy long fallback cannot be killed by a new message.
    """
    from apps.admin_ops import recovery as recovery_module

    stale_ids = list(
        Generation.objects.filter(
            user_message__conversation=conversation,
            state__in=ACTIVE_STATES,
            created_at__lt=recovery_module._cutoff(),
        )
        .order_by("created_at")
        .values_list("pk", flat=True)[:4]
    )
    recovered = 0
    for pk in stale_ids:
        try:
            recovered += int(recovery_module._recover_generation(pk))
        except Generation.DoesNotExist:
            continue
    return recovered


def _active_after_stale_recovery(conversation) -> bool:
    if not _active_generation_exists(conversation):
        return False
    _recover_stale_active_generations(conversation)
    return _active_generation_exists(conversation)


def _advisory_key(conversation_id) -> int:
    raw = hashlib.sha256(f"chat-single-flight:{conversation_id}".encode("utf-8")).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


def _busy_error():
    return ValidationError(
        "Предыдущий запрос ещё принимается. Дождитесь подтверждения или остановите его перед новым сообщением."
    )


@contextmanager
def _conversation_lock(conversation_id):
    """Authoritative bounded lock for one conversation.

    PostgreSQL advisory locks are session-owned and disappear if the connection dies.
    ``pg_try_advisory_lock`` keeps a wedged request from making the next HTTP request
    wait forever: after a short bounded period the client gets a recoverable busy
    response instead. Unrelated conversations use different lock keys.
    """
    if connection.vendor == "postgresql":
        key = _advisory_key(conversation_id)
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        acquired = False
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
                acquired = bool(cursor.fetchone()[0])
            if acquired:
                break
            time.sleep(LOCK_POLL_SECONDS)
        if not acquired:
            raise _busy_error()
        try:
            yield
        finally:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
            except Exception:
                # If the connection was lost PostgreSQL already released the lock.
                pass
        return

    name = str(conversation_id)
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.setdefault(name, threading.Lock())
    if not lock.acquire(timeout=LOCK_WAIT_SECONDS):
        raise _busy_error()
    try:
        yield
    finally:
        lock.release()


def install(streaming_module) -> None:
    """Serialize creation of new generations for one conversation.

    Idempotent replays remain legal. Every non-replay creation is rechecked while
    holding the authoritative conversation lock, preventing two tabs/processes from
    reserving money or starting providers concurrently for the same conversation.
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
        if _active_after_stale_recovery(conversation):
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
        # Fast replay/active checks avoid unnecessary lock waits, but correctness is
        # established only by the identical checks repeated under the advisory lock.
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
        if _active_after_stale_recovery(conversation):
            raise ValidationError(
                "Предыдущий ответ ещё формируется. Дождитесь завершения или остановите его перед новым сообщением."
            )

        with _conversation_lock(conversation.id):
            return execute_after_lock(
                user=user,
                conversation=conversation,
                content=content,
                client_message_id=client_message_id,
                idempotency_key=idempotency_key,
                file_ids=file_ids,
            )

    prepare._ai_workspace_single_flight = True
    prepare._raw_prepare = raw_prepare
    streaming_module.prepare = prepare

    # Do not rely on Django import order. Any entrypoint imported before AppConfig.ready()
    # must be rebound to the same authoritative single-flight prepare callable.
    for module_name in (
        "apps.chat.services",
        "apps.chat.views",
        "apps.chat.cost_views",
        "apps.chat.activity_stream",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "prepare"):
            module.prepare = prepare
