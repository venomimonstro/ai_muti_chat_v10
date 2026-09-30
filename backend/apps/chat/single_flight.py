from __future__ import annotations

import os
import sys

from django.core.cache import cache
from django.core.exceptions import ValidationError

from .models import Generation, Message

LOCK_SECONDS = max(15, int(os.getenv("CHAT_PREPARE_LOCK_SECONDS", "180")))
ACTIVE_STATES = {Generation.State.QUEUED, Generation.State.RUNNING}


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


def install(streaming_module) -> None:
    """Serialize creation of new generations for one conversation.

    Idempotent replays remain legal. A genuinely new request is rejected while a
    previous generation is QUEUED/RUNNING, preventing double-click, multi-tab and
    reconnect races from producing two concurrent answers against the same history.
    The cache lock only protects the short prepare race; durable Generation state is
    the source of truth afterwards.
    """
    raw_prepare = streaming_module.prepare
    if getattr(raw_prepare, "_ai_workspace_single_flight", False):
        return

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
        if not cache.add(key, "1", timeout=LOCK_SECONDS):
            raise ValidationError(
                "Предыдущий запрос ещё принимается. Повторите отправку после его подтверждения."
            )
        try:
            # Close the race between the first optimistic check and lock acquisition.
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
        finally:
            cache.delete(key)

    prepare._ai_workspace_single_flight = True
    prepare._raw_prepare = raw_prepare
    streaming_module.prepare = prepare

    # Rebind modules that may have imported prepare by value before AppConfig.ready().
    for module_name in ("apps.chat.services", "apps.chat.views"):
        module = sys.modules.get(module_name)
        if module is not None:
            module.prepare = prepare
