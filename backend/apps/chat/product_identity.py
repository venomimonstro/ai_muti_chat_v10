import json
import logging
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.workspace_search.embeddings import index_message

from .branches import ensure_active_branch
from .models import Conversation, Generation, Message

logger = logging.getLogger(__name__)
ZERO = Decimal("0.0000")
PUBLIC_SYSTEM_LEVELS = {
    Conversation.RoutingMode.ECONOMY: "System Lite",
    Conversation.RoutingMode.BALANCED: "System Pro",
    Conversation.RoutingMode.MAXIMUM: "System Max",
}

IDENTITY_QUESTIONS = {
    "кто ты",
    "ты кто",
    "кто ты такой",
    "кто ты такая",
    "представься",
}
CREATOR_QUESTIONS = {
    "кто тебя создал",
    "кто тебя разработал",
    "кто твой создатель",
    "кто твой разработчик",
    "кем ты создан",
    "кем ты создана",
}


def _normalize_question(value):
    value = str(value or "").casefold().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я]+", " ", value, flags=re.IGNORECASE)
    return " ".join(value.split())


def direct_identity_answer(content, file_ids=None):
    """Return a deterministic local answer only for direct product-identity questions.

    Longer/mixed prompts intentionally go to the normal model so this shortcut can
    never swallow a real task merely because it contains the words «кто ты».
    """
    if file_ids:
        return None
    normalized = _normalize_question(content)
    if normalized in IDENTITY_QUESTIONS:
        return "Я ваш агент."
    if normalized in CREATOR_QUESTIONS:
        return "Компания BBTEC."
    return None


def public_system_level(conversation):
    return PUBLIC_SYSTEM_LEVELS.get(conversation.routing_mode, "System Pro")


def identity_preview(conversation):
    level = public_system_level(conversation)
    return {
        "estimated_min_rub": ZERO,
        "estimated_max_rub": ZERO,
        "confirmation_required": False,
        "confirmation_threshold_rub": ZERO,
        "selected_model": level,
        "models": [{"model": level, "display_name": level, "estimated_max_rub": "0.0000"}],
        "spend_guard": {},
        "blocked_by_spend_guard": False,
        "spend_guard_message": "",
    }


def _validate_existing(generation, *, conversation, content, client_message_id):
    message = generation.user_message
    if (
        message.conversation_id != conversation.id
        or message.content != content
        or message.client_message_id != client_message_id
        or not generation.context_snapshot.get("local_product_identity")
    ):
        raise ValidationError("Idempotency-Key уже использован для другого запроса")
    return generation


def create_identity_generation(
    *, user, conversation, content, client_message_id, idempotency_key, answer
):
    """Persist a zero-cost identity answer with the same replay guarantees as chat generation."""
    existing = (
        Generation.objects.filter(owner=user, idempotency_key=idempotency_key)
        .select_related("user_message", "assistant_message")
        .first()
    )
    if existing is not None:
        return _validate_existing(
            existing,
            conversation=conversation,
            content=content,
            client_message_id=client_message_id,
        ), False

    with transaction.atomic():
        user.__class__.objects.select_for_update().only("pk").get(pk=user.pk)
        existing = (
            Generation.objects.filter(owner=user, idempotency_key=idempotency_key)
            .select_related("user_message", "assistant_message")
            .first()
        )
        if existing is not None:
            return _validate_existing(
                existing,
                conversation=conversation,
                content=content,
                client_message_id=client_message_id,
            ), False

        locked = Conversation.objects.select_for_update().get(pk=conversation.pk, owner=user)
        repeated = (
            Message.objects.filter(
                conversation=locked,
                client_message_id=client_message_id,
                role=Message.Role.USER,
            )
            .select_related("generation_request__assistant_message")
            .first()
        )
        if repeated is not None:
            if repeated.content != content:
                raise ValidationError("client_message_id уже использован с другим содержимым")
            try:
                generation = repeated.generation_request
            except Message.generation_request.RelatedObjectDoesNotExist:
                raise ValidationError("Повторное сообщение ещё не готово к обработке") from None
            return _validate_existing(
                generation,
                conversation=locked,
                content=content,
                client_message_id=client_message_id,
            ), False

        branch = ensure_active_branch(locked, user)
        user_message = Message.objects.create(
            conversation=locked,
            branch=branch,
            role=Message.Role.USER,
            content=content,
            client_message_id=client_message_id,
            status=Message.Status.SAVED,
        )
        assistant = Message.objects.create(
            conversation=locked,
            branch=branch,
            role=Message.Role.ASSISTANT,
            content=answer,
            status=Message.Status.COMPLETED,
        )
        level = public_system_level(locked)
        routing = {
            "decision_id": "",
            "mode": locked.routing_mode,
            "task_taxonomy": "product_identity",
            "selected_model": level,
            "model_version": level,
            "exact_api_id": "",
            "explanation": f"Использован уровень {level}.",
            "policy_version": "local-product-identity-v1",
            "classification_confidence": 1.0,
            "required_capabilities": [],
            "estimated_cost_rub": "0.0000",
            "candidates": [],
        }
        generation = Generation.objects.create(
            owner=user,
            user_message=user_message,
            assistant_message=assistant,
            state=Generation.State.COMPLETED,
            model=level,
            routed_model=level,
            provider_slug="system",
            idempotency_key=idempotency_key,
            actual_cost_rub=ZERO,
            completed_at=timezone.now(),
            context_snapshot={"local_product_identity": True, "routing": routing},
        )

    for message in (user_message, assistant):
        try:
            index_message(message)
        except Exception:
            logger.exception("Identity history indexing failed message_id=%s", message.id)
    return generation, True


def identity_sse(generation):
    text = generation.assistant_message.content
    level = generation.model or "System Pro"
    yield (
        "event: generation\n"
        f'data: {{"id":"{generation.id}","state":"streaming","correlation_id":"{generation.correlation_id}"}}\n\n'
    )
    yield f"event: delta\ndata: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
    yield "event: completed\ndata: " + json.dumps(
        {
            "state": "completed",
            "cost_rub": "0.0000",
            "input_tokens": 0,
            "output_tokens": 0,
            "model": level,
            "model_version": level,
            "provider": "system",
        },
        ensure_ascii=False,
    ) + "\n\n"
