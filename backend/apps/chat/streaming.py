import json
import logging
import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import (
    candidate_models,
    provider_available,
    record_failure,
    record_success,
)
from apps.ai_registry.router import select_route
from apps.billing.models import BalanceReservation, RequestCost
from apps.billing.pricing import (
    active_price,
    calculate,
    calculate_from_snapshot,
    conservative_token_budget,
    quote,
    require_margin,
)
from apps.billing.reconciliation import record_cost_outcome
from apps.billing.services import release, reserve, settle
from apps.memory_store.services import (
    extract_memory_candidates,
    process_explicit_command,
    record_memory_usage,
)
from apps.workspace_search.embeddings import index_message

from .branches import ensure_active_branch
from .context import assemble_context, refresh_rolling_summary
from .models import Conversation, Generation, GenerationAttempt, Message, RoutingDecision

MAX_OUTPUT_TOKENS = 1024
FLUSH_CHARS = 2000
logger = logging.getLogger(__name__)


def sse(event: str, data: dict):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _index_history(message):
    try:
        index_message(message)
    except Exception:
        logger.exception("History indexing failed for message_id=%s", message.id)


def _validate_replayed_generation(generation, conversation, content, client_message_id):
    message = generation.user_message
    if (
        message.conversation_id != conversation.id
        or message.content != content
        or message.client_message_id != client_message_id
    ):
        raise ValidationError("Idempotency-Key уже использован для другого запроса")
    return generation


def prepare(*, user, conversation, content, client_message_id, idempotency_key):
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValidationError("Корректный Idempotency-Key обязателен")
    if not isinstance(content, str) or not content.strip() or len(content) > 100_000:
        raise ValidationError("Сообщение должно содержать от 1 до 100000 символов")
    existing = (
        Generation.objects.filter(idempotency_key=idempotency_key, owner=user)
        .select_related("assistant_message", "user_message")
        .first()
    )
    if existing:
        return _validate_replayed_generation(
            existing, conversation, content, client_message_id
        ), False

    with transaction.atomic():
        user.__class__.objects.select_for_update().only("pk").get(pk=user.pk)
        existing = (
            Generation.objects.filter(idempotency_key=idempotency_key, owner=user)
            .select_related("assistant_message", "user_message")
            .first()
        )
        if existing:
            return _validate_replayed_generation(
                existing, conversation, content, client_message_id
            ), False
        locked = Conversation.objects.select_for_update().get(pk=conversation.pk, owner=user)
        repeated_message = (
            Message.objects.filter(
                conversation=locked, client_message_id=client_message_id, role=Message.Role.USER
            )
            .select_related("generation_request__assistant_message")
            .first()
        )
        if repeated_message is not None:
            if repeated_message.content != content:
                raise ValidationError("client_message_id уже использован с другим содержимым")
            try:
                return repeated_message.generation_request, False
            except Message.generation_request.RelatedObjectDoesNotExist:
                raise ValidationError("Повторное сообщение ещё не готово к обработке") from None
        branch = ensure_active_branch(locked, user)
        user_message = Message.objects.create(
            conversation=locked,
            branch=branch,
            role=Message.Role.USER,
            content=content,
            client_message_id=client_message_id,
            status=Message.Status.SAVED,
        )
        assistant_message = Message.objects.create(
            conversation=locked,
            branch=branch,
            role=Message.Role.ASSISTANT,
            status=Message.Status.SAVED,
        )
        generation = Generation.objects.create(
            owner=user,
            user_message=user_message,
            assistant_message=assistant_message,
            model=locked.selected_model,
            idempotency_key=idempotency_key,
        )
    _index_history(user_message)

    memory_action, suppress_memory = process_explicit_command(
        user=user, conversation=conversation, source_message=user_message
    )
    memory_candidates = (
        []
        if memory_action or suppress_memory
        else extract_memory_candidates(
            user=user, conversation=conversation, source_message=user_message
        )
    )
    memory_metadata = {
        "memory_action": memory_action,
        "memory_candidates": [str(candidate.id) for candidate in memory_candidates],
    }

    try:
        route = select_route(conversation=locked, content=content)
        generation.model = route.selected.slug
        generation.save(update_fields=["model"])
        decision = RoutingDecision.objects.create(
            generation=generation,
            policy=route.policy,
            mode=conversation.routing_mode,
            task_taxonomy=route.classification.taxonomy,
            classification_confidence=route.classification.confidence,
            required_capabilities=route.classification.required_capabilities,
            signals=route.classification.signals,
            selected_model=route.selected,
            candidate_snapshot=route.candidates,
            explanation=route.explanation,
            estimated_input_tokens=route.estimated_input_tokens,
            estimated_output_tokens=route.estimated_output_tokens,
            estimated_cost_rub=route.estimated_cost_rub,
        )
        candidates = route.ordered_models
        if not candidates:
            raise ValidationError("Выбранная модель временно недоступна")
        narrowest = min(candidates, key=lambda item: item.context_window)
        snapshot, memory_items = assemble_context(
            user=user,
            conversation=conversation,
            assistant_message=assistant_message,
            model=narrowest,
            output_tokens=MAX_OUTPUT_TOKENS,
            include_memory=not suppress_memory,
        )
        snapshot.update(memory_metadata)
        snapshot["routing"] = {
            "decision_id": str(decision.id),
            "mode": decision.mode,
            "task_taxonomy": decision.task_taxonomy,
            "selected_model": route.selected.slug,
            "model_version": (
                route.selected.current_version.version if route.selected.current_version else None
            ),
            "exact_api_id": route.selected.upstream_model,
            "explanation": decision.explanation,
            "policy_version": route.policy.version,
            "classification_confidence": float(decision.classification_confidence),
            "required_capabilities": decision.required_capabilities,
            "estimated_cost_rub": str(decision.estimated_cost_rub),
            "candidates": decision.candidate_snapshot,
        }
        snapshot["memory_items"] = [
            {
                "id": str(item.id),
                "scope": item.scope,
                "memory_type": item.memory_type,
                "content": item.content,
            }
            for item in memory_items
        ]
        generation.context_snapshot = snapshot
        generation.save(update_fields=["context_snapshot"])
        record_memory_usage(generation, memory_items)
        history = snapshot["provider_messages"]
        input_budget, output_budget = conservative_token_budget(
            history, snapshot["budget"]["output_reserved"]
        )
        priced = []
        for model in candidates:
            price = active_price(model.slug)
            price_quote = require_margin(
                quote(
                    price,
                    input_budget,
                    output_budget,
                    provider_slug=model.provider.slug,
                    model_slug=model.slug,
                )
            )
            priced.append((model, price, price_quote))
        estimates = [item.user_charge_rub for _, _, item in priced]
        estimated = max(estimates)
        reservation = reserve(user, estimated, f"generation:{generation.id}")
        selected_quote = priced[0][2]
        RequestCost.objects.create(
            generation_id=generation.id,
            price_version=priced[0][1],
            estimated_rub=estimated,
            expected_provider_cost_rub=selected_quote.provider_cost_rub,
            fx_snapshot=selected_quote.fx_snapshot,
            pricing_snapshot=selected_quote.pricing_snapshot,
            model_version_id_snapshot=priced[0][0].current_version_id,
        )
        generation.reservation_id = reservation.id
        generation.route_price_snapshot = {
            model.slug: {
                "price_version_id": str(price.id),
                "fx_snapshot_id": str(item.fx_snapshot.id),
                "pricing_snapshot": item.pricing_snapshot,
                "expected_provider_cost_rub": str(item.provider_cost_rub),
                "model_version_id": (
                    str(model.current_version_id) if model.current_version_id else None
                ),
            }
            for model, price, item in priced
        }
        generation.state = Generation.State.QUEUED
        generation.save(update_fields=["reservation_id", "route_price_snapshot", "state"])
    except Exception:
        if generation.reservation_id:
            release(generation.reservation_id)
        else:
            stranded = BalanceReservation.objects.filter(
                idempotency_key=f"generation:{generation.id}",
                state=BalanceReservation.State.ACTIVE,
            ).first()
            if stranded:
                release(stranded.id)
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
    claimed = Generation.objects.filter(
        pk=generation.pk, state=Generation.State.QUEUED
    ).update(state=Generation.State.RUNNING)
    if claimed:
        generation.state = Generation.State.RUNNING
    else:
        generation.refresh_from_db(fields=["state", "error_code"])
    if generation.state == Generation.State.RUNNING and not claimed:
        yield sse("error", {"code": "generation_in_progress"})
        return
    if generation.state != Generation.State.RUNNING:
        yield sse("error", {"code": generation.error_code or "generation_not_runnable"})
        return

    primary = AIModel.objects.select_related("provider", "fallback_model", "current_version").get(
        slug=generation.model
    )
    if adapter:
        candidates = [primary]
    else:
        try:
            snapshot = generation.routing_decision.candidate_snapshot
            ranked = sorted(
                (
                    item
                    for item in snapshot
                    if item.get("status") == "eligible" and item.get("fallback_allowed", True)
                ),
                key=lambda item: item.get("rank", 9999),
            )
            models = {
                item.slug: item
                for item in AIModel.objects.filter(
                    slug__in=[item["model"] for item in ranked], enabled=True
                ).select_related("provider", "fallback_model", "current_version")
            }
            candidates = [
                models[item["model"]]
                for item in ranked
                if item["model"] in models and provider_available(models[item["model"]].provider)
            ]
        except RoutingDecision.DoesNotExist:
            candidates = candidate_models(primary)
    history = generation.context_snapshot.get("provider_messages") or [
        {"role": generation.user_message.role, "content": generation.user_message.content}
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
        routing = generation.routing_decision
        if routing.mode != Conversation.RoutingMode.MANUAL:
            yield sse(
                "routing",
                {
                    "mode": routing.mode,
                    "task_taxonomy": routing.task_taxonomy,
                    "model": routing.selected_model.slug,
                    "model_version": (
                        routing.selected_model.current_version.version
                        if routing.selected_model.current_version
                        else None
                    ),
                    "explanation": routing.explanation,
                },
            )
    except RoutingDecision.DoesNotExist:
        pass
    if generation.context_snapshot.get("memory_action"):
        yield sse("memory", generation.context_snapshot["memory_action"])
    if generation.context_snapshot.get("memory_candidates"):
        yield sse(
            "memory_candidates",
            {
                "count": len(generation.context_snapshot["memory_candidates"]),
                "message": "Найдены предложения для памяти",
            },
        )
    try:
        for model in candidates:
            request_cost = RequestCost.objects.get(generation_id=generation.id)
            route_price = generation.route_price_snapshot.get(model.slug)
            if not route_price:
                raise ValidationError("Missing route price snapshot")
            if isinstance(route_price, str):
                request_cost.price_version_id = route_price
                fields = ["price_version"]
            else:
                request_cost.price_version_id = route_price["price_version_id"]
                request_cost.fx_snapshot_id = route_price["fx_snapshot_id"]
                request_cost.pricing_snapshot = route_price["pricing_snapshot"]
                request_cost.expected_provider_cost_rub = route_price["expected_provider_cost_rub"]
                request_cost.model_version_id_snapshot = route_price["model_version_id"]
                fields = [
                    "price_version",
                    "fx_snapshot",
                    "pricing_snapshot",
                    "expected_provider_cost_rub",
                    "model_version_id_snapshot",
                ]
            request_cost.save(update_fields=fields)
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

        with transaction.atomic():
            request_cost = RequestCost.objects.select_for_update().select_related(
                "price_version"
            ).get(generation_id=generation.id)
            if request_cost.pricing_snapshot:
                provider_cost, charge, gross_profit, gross_margin = calculate_from_snapshot(
                    request_cost.price_version,
                    completed.input_tokens,
                    completed.output_tokens,
                    request_cost.pricing_snapshot,
                )
            else:
                provider_cost, charge = calculate(
                    request_cost.price_version, completed.input_tokens, completed.output_tokens
                )
                gross_profit = charge - provider_cost
                gross_margin = gross_profit / charge * 100 if charge else 100
            reservation_amount = (
                generation.user_message.conversation.owner.wallet.reservations.get(
                    id=generation.reservation_id
                ).amount_rub
            )
            if charge > reservation_amount:
                raise ValidationError("Provider usage exceeded reserved maximum")
            settle(generation.reservation_id, charge)
            request_cost.provider_cost_rub = provider_cost
            request_cost.charged_rub = charge
            request_cost.input_tokens = completed.input_tokens
            request_cost.output_tokens = completed.output_tokens
            request_cost.gross_profit_rub = gross_profit
            request_cost.gross_margin_percent = gross_margin
            request_cost.save(
                update_fields=[
                    "provider_cost_rub",
                    "charged_rub",
                    "input_tokens",
                    "output_tokens",
                    "gross_profit_rub",
                    "gross_margin_percent",
                ]
            )
            record_cost_outcome(request_cost, model=selected_model)
            assistant.content = full_text
            assistant.status = Message.Status.COMPLETED
            assistant.save(update_fields=["content", "status"])
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
        _index_history(assistant)
        refresh_rolling_summary(generation.user_message.conversation)
        yield sse(
            "completed",
            {
                "state": "completed",
                "cost_rub": charge,
                "input_tokens": completed.input_tokens,
                "output_tokens": completed.output_tokens,
                "model": selected_model.slug,
                "model_version": (
                    selected_model.current_version.version
                    if selected_model.current_version
                    else None
                ),
                "provider": selected_model.provider.slug,
            },
        )
    except GeneratorExit:
        release(generation.reservation_id)
        assistant.content = full_text
        assistant.status = Message.Status.PARTIAL if full_text else Message.Status.FAILED
        assistant.save(update_fields=["content", "status"])
        _index_history(assistant)
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
        _index_history(assistant)
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
