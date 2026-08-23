from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.ai_registry.adapters import EchoProviderAdapter, ProviderError
from apps.billing.services import release, reserve, settle

from .models import Conversation, Generation, Message

MAX_RESERVE_RUB = Decimal("2.0000")
MOCK_ACTUAL_COST_RUB = Decimal("0.0500")


def generate_reply(
    *,
    user,
    conversation: Conversation,
    content: str,
    client_message_id,
    idempotency_key: str,
    adapter=None,
):
    existing = (
        Generation.objects.filter(
            idempotency_key=idempotency_key, user_message__conversation__owner=user
        )
        .select_related("assistant_message")
        .first()
    )
    if existing:
        return existing

    with transaction.atomic():
        locked = Conversation.objects.select_for_update().get(pk=conversation.pk, owner=user)
        user_message = Message.objects.create(
            conversation=locked,
            role=Message.Role.USER,
            content=content,
            client_message_id=client_message_id,
            status=Message.Status.SAVED,
        )
        assistant_message = Message.objects.create(
            conversation=locked,
            role=Message.Role.ASSISTANT,
            content="",
            status=Message.Status.SAVED,
        )
        generation = Generation.objects.create(
            user_message=user_message,
            assistant_message=assistant_message,
            model=locked.selected_model,
            idempotency_key=idempotency_key,
        )

    reservation = reserve(user, MAX_RESERVE_RUB, f"generation:{generation.id}")
    generation.reservation_id = reservation.id
    generation.state = Generation.State.RUNNING
    generation.save(update_fields=["reservation_id", "state"])
    adapter = adapter or EchoProviderAdapter()
    try:
        history = [
            {"role": m.role, "content": m.content}
            for m in conversation.messages.exclude(id=assistant_message.id)
        ]
        result = adapter.generate(model=generation.model, messages=history, max_output_tokens=1024)
        assistant_message.content = result.text
        assistant_message.status = Message.Status.COMPLETED
        assistant_message.save(update_fields=["content", "status"])
        settle(reservation.id, MOCK_ACTUAL_COST_RUB)
        generation.state = Generation.State.COMPLETED
        generation.provider_request_id = result.provider_request_id
        generation.input_tokens = result.input_tokens
        generation.output_tokens = result.output_tokens
        generation.actual_cost_rub = MOCK_ACTUAL_COST_RUB
        generation.completed_at = timezone.now()
        generation.save(
            update_fields=[
                "state",
                "provider_request_id",
                "input_tokens",
                "output_tokens",
                "actual_cost_rub",
                "completed_at",
            ]
        )
    except Exception as exc:
        release(reservation.id)
        assistant_message.status = Message.Status.FAILED
        assistant_message.save(update_fields=["status"])
        generation.state = Generation.State.FAILED
        generation.error_code = (
            "provider_error" if isinstance(exc, ProviderError) else "internal_error"
        )
        generation.completed_at = timezone.now()
        generation.save(update_fields=["state", "error_code", "completed_at"])
    return generation
