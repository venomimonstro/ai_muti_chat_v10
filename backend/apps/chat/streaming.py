import json
import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import candidate_models, record_failure, record_success
from apps.billing.models import RequestCost
from apps.billing.pricing import active_price, calculate, conservative_token_budget
from apps.billing.services import release, reserve, settle

from .models import Conversation, Generation, GenerationAttempt, Message

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
        primary = AIModel.objects.select_related("provider", "fallback_model").get(
            slug=generation.model, enabled=True
        )
        candidates = candidate_models(primary)
        if not candidates:
            raise ValidationError("Выбранная модель временно недоступна")
        history = [
            {"role": item.role, "content": item.content}
            for item in conversation.messages.exclude(id=assistant_message.id)
        ]
        input_budget, output_budget = conservative_token_budget(history, MAX_OUTPUT_TOKENS)
        priced = [(model, active_price(model.slug)) for model in candidates]
        estimates = [calculate(price, input_budget, output_budget)[1] for _, price in priced]
        estimated = max(estimates)
        reservation = reserve(user, estimated, f"generation:{generation.id}")
        RequestCost.objects.create(
            generation_id=generation.id,
            price_version=priced[0][1],
            estimated_rub=estimated,
        )
        generation.reservation_id = reservation.id
        generation.route_price_snapshot = {model.slug: str(price.id) for model, price in priced}
        generation.state = Generation.State.RUNNING
        generation.save(update_fields=["reservation_id", "route_price_snapshot", "state"])
    except Exception:
        generation.state = Generation.State.FAILED
        generation.error_code = "preflight_failed"
        generation.completed_at = timezone.now()
        generation.save(update_fields=["state", "error_code", "completed_at"])
        raise
    return generation, True


def _finish_attempt(attempt, *, state, started, error=None):
    attempt.state = state
    attempt.latency_ms = int((time.monotonic() - started) * 1000)
    attempt.finished_at = timezone.now()
    fields = ["state", "latency_ms", "finished_at"]
    if error:
        attempt.error_code = error.code
        attempt.retryable = error.retryable
        fields += ["error_code", "retryable"]
    attempt.save(update_fields=fields)


def run(generation, *, adapter=None):
    assistant = generation.assistant_message
    if generation.state == Generation.State.COMPLETED:
        yield sse("snapshot", {"text": assistant.content, "state": generation.state})
        return
    if generation.state != Generation.State.RUNNING:
        yield sse("error", {"code": generation.error_code or "generation_not_runnable"})
        return

    primary = AIModel.objects.select_related("provider", "fallback_model").get(
        slug=generation.model
    )
    candidates = [primary] if adapter else candidate_models(primary)
    history = [
        {"role": item.role, "content": item.content}
        for item in generation.user_message.conversation.messages.exclude(id=assistant.id)
    ]
    full_text = assistant.content
    sequence = generation.attempts.count()
    completed = None
    selected_model = None
    last_error = ProviderError("No healthy provider", code="provider_unavailable")

    yield sse(
        "generation",
        {"id": generation.id, "state": "streaming", "correlation_id": generation.correlation_id},
    )
    try:
        for model in candidates:
            request_cost = RequestCost.objects.get(generation_id=generation.id)
            price_id = generation.route_price_snapshot.get(model.slug)
            if not price_id:
                raise ValidationError("Missing route price snapshot")
            request_cost.price_version_id = price_id
            request_cost.save(update_fields=["price_version"])
            max_attempts = 1 if adapter else settings.AI_PROVIDER_MAX_ATTEMPTS
            for retry_index in range(max_attempts):
                sequence += 1
                attempt = GenerationAttempt.objects.create(
                    generation=generation,
                    provider=model.provider,
                    model_slug=model.slug,
                    sequence=sequence,
                )
                started = time.monotonic()
                emitted = False
                attempt_completed = None
                try:
                    provider_adapter = adapter or adapter_for(model)
                    for event in provider_adapter.stream(
                        model=model.upstream_model,
                        messages=history,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                    ):
                        if event.kind == "delta":
                            emitted = True
                            full_text += event.text_delta
                            yield sse("delta", {"text": event.text_delta})
                            if len(full_text) - len(assistant.content) >= FLUSH_CHARS:
                                assistant.content = full_text
                                assistant.status = Message.Status.STREAMING
                                assistant.save(update_fields=["content", "status"])
                        else:
                            attempt_completed = event
                    if attempt_completed is None:
                        raise ProviderError(
                            "Stream ended without usage", code="invalid_stream", retryable=True
                        )
                    latency = int((time.monotonic() - started) * 1000)
                    _finish_attempt(
                        attempt, state=GenerationAttempt.State.COMPLETED, started=started
                    )
                    record_success(model.provider, latency)
                    completed = attempt_completed
                    selected_model = model
                    break
                except ProviderError as exc:
                    last_error = exc
                    _finish_attempt(
                        attempt,
                        state=GenerationAttempt.State.FAILED,
                        started=started,
                        error=exc,
                    )
                    record_failure(model.provider, exc)
                    if emitted:
                        raise
                    if exc.retryable and retry_index + 1 < max_attempts:
                        yield sse("recovery", {"action": "retry", "provider": model.provider.slug})
                        continue
                    break
            if selected_model:
                break
            if model != candidates[-1]:
                yield sse("recovery", {"action": "fallback", "from_model": model.slug})

        if selected_model is None or completed is None:
            raise last_error

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
        generation.routed_model = selected_model.slug
        generation.provider_slug = selected_model.provider.slug
        generation.completed_at = timezone.now()
        generation.save(
            update_fields=[
                "state",
                "provider_request_id",
                "input_tokens",
                "output_tokens",
                "actual_cost_rub",
                "routed_model",
                "provider_slug",
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
                "model": selected_model.slug,
                "provider": selected_model.provider.slug,
            },
        )
    except GeneratorExit:
        release(generation.reservation_id)
        assistant.content = full_text
        assistant.status = Message.Status.PARTIAL if full_text else Message.Status.FAILED
        assistant.save(update_fields=["content", "status"])
        generation.state = Generation.State.CANCELLED
        generation.error_code = "client_cancelled"
        generation.completed_at = timezone.now()
        generation.save(update_fields=["state", "error_code", "completed_at"])
        return
    except Exception as exc:
        release(generation.reservation_id)
        assistant.content = full_text
        assistant.status = Message.Status.PARTIAL if full_text else Message.Status.FAILED
        assistant.save(update_fields=["content", "status"])
        generation.state = Generation.State.FAILED
        generation.error_code = (
            exc.code if isinstance(exc, ProviderError) else "cost_or_internal_error"
        )
        generation.completed_at = timezone.now()
        generation.save(update_fields=["state", "error_code", "completed_at"])
        yield sse(
            "error",
            {
                "code": generation.error_code,
                "partial": bool(full_text),
                "message": "Провайдер временно недоступен. Запрос сохранён, деньги не списаны.",
                "correlation_id": generation.correlation_id,
            },
        )
