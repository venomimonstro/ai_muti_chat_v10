import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.models import AIModel
from apps.billing.models import RequestCost
from apps.billing.pricing import active_price, calculate, conservative_token_budget
from apps.billing.services import release, reserve, settle

from .models import Conversation, Generation, Message

MAX_OUTPUT_TOKENS = 1024
FLUSH_CHARS = 400


def sse(event: str, data: dict):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def prepare(*, user, conversation, content, client_message_id, idempotency_key):
    existing = (
        Generation.objects.filter(
            idempotency_key=idempotency_key, user_message__conversation__owner=user
        )
        .select_related("assistant_message", "user_message")
        .first()
    )
    if existing:
        return existing, False

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
            conversation=locked, role=Message.Role.ASSISTANT, status=Message.Status.SAVED
        )
        generation = Generation.objects.create(
            user_message=user_message,
            assistant_message=assistant_message,
            model=locked.selected_model,
            idempotency_key=idempotency_key,
        )

    try:
        model = AIModel.objects.select_related("provider").get(
            slug=generation.model, enabled=True, provider__enabled=True
        )
        price = active_price(model.slug)
        history = [
            {"role": item.role, "content": item.content}
            for item in conversation.messages.exclude(id=assistant_message.id)
        ]
        input_budget, output_budget = conservative_token_budget(history, MAX_OUTPUT_TOKENS)
        _, estimated = calculate(price, input_budget, output_budget)
        reservation = reserve(user, estimated, f"generation:{generation.id}")
        RequestCost.objects.create(
            generation_id=generation.id, price_version=price, estimated_rub=estimated
        )
        generation.reservation_id = reservation.id
        generation.state = Generation.State.RUNNING
        generation.save(update_fields=["reservation_id", "state"])
    except Exception:
        generation.state = Generation.State.FAILED
        generation.error_code = "preflight_failed"
        generation.completed_at = timezone.now()
        generation.save(update_fields=["state", "error_code", "completed_at"])
        raise
    return generation, True


def run(generation, *, adapter=None):
    assistant = generation.assistant_message
    if generation.state == Generation.State.COMPLETED:
        yield sse("snapshot", {"text": assistant.content, "state": generation.state})
        return
    if generation.state != Generation.State.RUNNING:
        yield sse("error", {"code": generation.error_code or "generation_not_runnable"})
        return

    model = AIModel.objects.select_related("provider").get(slug=generation.model)
    adapter = adapter or adapter_for(model)
    history = [
        {"role": item.role, "content": item.content}
        for item in generation.user_message.conversation.messages.exclude(id=assistant.id)
    ]
    buffer = ""
    full_text = assistant.content
    completed = None
    try:
        yield sse("generation", {"id": generation.id, "state": "streaming"})
        for event in adapter.stream(
            model=model.upstream_model,
            messages=history,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        ):
            if event.kind == "delta":
                buffer += event.text_delta
                full_text += event.text_delta
                yield sse("delta", {"text": event.text_delta})
                if len(buffer) >= FLUSH_CHARS:
                    assistant.content = full_text
                    assistant.status = Message.Status.STREAMING
                    assistant.save(update_fields=["content", "status"])
                    buffer = ""
            else:
                completed = event
        if completed is None:
            raise ProviderError("Stream ended without usage")
        assistant.content = full_text
        assistant.status = Message.Status.COMPLETED
        assistant.save(update_fields=["content", "status"])
        request_cost = RequestCost.objects.select_related("price_version").get(
            generation_id=generation.id
        )
        provider_cost, charge = calculate(
            request_cost.price_version, completed.input_tokens, completed.output_tokens
        )
        reservation_amount = generation.user_message.conversation.owner.wallet.reservations.get(
            id=generation.reservation_id
        ).amount_rub
        if charge > reservation_amount:
            raise ValidationError("Provider usage exceeded reserved maximum")
        settle(generation.reservation_id, charge)
        request_cost.provider_cost_rub = provider_cost
        request_cost.charged_rub = charge
        request_cost.input_tokens = completed.input_tokens
        request_cost.output_tokens = completed.output_tokens
        request_cost.save(
            update_fields=["provider_cost_rub", "charged_rub", "input_tokens", "output_tokens"]
        )
        generation.state = Generation.State.COMPLETED
        generation.provider_request_id = completed.provider_request_id
        generation.input_tokens = completed.input_tokens
        generation.output_tokens = completed.output_tokens
        generation.actual_cost_rub = charge
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
        yield sse(
            "completed",
            {
                "state": "completed",
                "cost_rub": charge,
                "input_tokens": completed.input_tokens,
                "output_tokens": completed.output_tokens,
            },
        )
    except Exception as exc:
        release(generation.reservation_id)
        assistant.content = full_text
        assistant.status = Message.Status.PARTIAL if full_text else Message.Status.FAILED
        assistant.save(update_fields=["content", "status"])
        generation.state = Generation.State.FAILED
        generation.error_code = (
            "provider_error" if isinstance(exc, ProviderError) else "cost_or_internal_error"
        )
        generation.completed_at = timezone.now()
        generation.save(update_fields=["state", "error_code", "completed_at"])
        yield sse("error", {"code": generation.error_code, "partial": bool(full_text)})
