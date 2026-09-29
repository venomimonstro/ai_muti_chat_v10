import json
import logging
import time

from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import provider_available, record_failure, record_success
from apps.billing.models import BalanceReservation, RequestCost
from apps.billing.pricing import active_price, calculate
from apps.billing.services import release

from .models import Generation, GenerationAttempt, Message
from .partial_billing import settle_delivered_partial
from .streaming import run

logger = logging.getLogger(__name__)


PUBLIC_SYSTEM_LEVELS = {
    "economy": "System Lite",
    "balanced": "System Pro",
    "maximum": "System Max",
}

PROVIDER_ERROR_MESSAGES = {
    "credit_balance_exhausted": (
        "У AI-провайдера закончились API-кредиты. Запрос сохранён, деньги не списаны. "
        "Администратору необходимо пополнить баланс провайдера."
    ),
    "organization_usage_limit_exceeded": (
        "AI-провайдер достиг лимита использования организации. Запрос сохранён, деньги не списаны."
    ),
    "organization_spend_limit_exceeded": (
        "AI-провайдер достиг лимита расходов организации. Запрос сохранён, деньги не списаны."
    ),
    "project_spend_limit_exceeded": (
        "AI-провайдер достиг лимита расходов проекта. Запрос сохранён, деньги не списаны."
    ),
    "invalid_api_key": (
        "Ключ AI-провайдера требует проверки администратором. Запрос сохранён, деньги не списаны."
    ),
    "authentication_error": (
        "AI-провайдер отклонил авторизацию. Запрос сохранён, деньги не списаны."
    ),
    "permission_denied": (
        "У ключа AI-провайдера недостаточно прав. Запрос сохранён, деньги не списаны."
    ),
    "model_not_found": (
        "Выбранная модель недоступна для текущего API-ключа. Запрос сохранён, деньги не списаны."
    ),
}

_PROVIDER_FAILURE_PREFIXES = (
    "gigachat_",
    "deepseek_",
    "openai_",
    "anthropic_",
    "provider_",
)
_PROVIDER_FAILURE_CODES = {
    "timeout",
    "invalid_stream",
    "network_error",
    "rate_limited",
    "authentication_error",
    "permission_denied",
    "model_not_found",
    "invalid_api_key",
    "credit_balance_exhausted",
    "organization_usage_limit_exceeded",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
}


def _finalize_unhandled_disconnect(generation):
    generation.refresh_from_db(fields=["state", "reservation_id", "actual_cost_rub"])
    if generation.state not in {Generation.State.QUEUED, Generation.State.RUNNING}:
        return
    assistant = generation.assistant_message
    assistant.refresh_from_db(fields=["content", "status"])
    try:
        charge = settle_delivered_partial(generation, assistant.content)
    except Exception:
        logger.exception(
            "Managed stream cancellation settlement failed generation_id=%s",
            generation.id,
        )
        try:
            closed = release(generation.reservation_id)
            charge = closed.actual_rub or 0
        except Exception:
            logger.exception(
                "Managed stream reservation release failed generation_id=%s",
                generation.id,
            )
            charge = 0
    assistant.status = Message.Status.PARTIAL if assistant.content else Message.Status.FAILED
    assistant.save(update_fields=["status"])
    generation.state = Generation.State.CANCELLED
    generation.error_code = "client_cancelled"
    generation.actual_cost_rub = charge
    generation.completed_at = timezone.now()
    generation.save(
        update_fields=["state", "error_code", "actual_cost_rub", "completed_at"]
    )


def _reservation_actual(generation):
    if not generation.reservation_id:
        return None
    return BalanceReservation.objects.filter(pk=generation.reservation_id).values_list(
        "actual_rub", flat=True
    ).first()


def _parse_error_chunk(chunk):
    if not isinstance(chunk, str) or not chunk.startswith("event: error\n"):
        return None
    try:
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        return json.loads(data_line[6:])
    except Exception:
        return None


def _rewrite_error_chunk_if_needed(generation, chunk):
    payload = _parse_error_chunk(chunk)
    if payload is None:
        return chunk

    code = str(payload.get("code") or "")
    actual = _reservation_actual(generation)
    if actual is None:
        if code in PROVIDER_ERROR_MESSAGES:
            payload["message"] = PROVIDER_ERROR_MESSAGES[code]
            return f"event: error\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
        return chunk

    generation.refresh_from_db(fields=["state", "actual_cost_rub"])
    if generation.actual_cost_rub != actual:
        Generation.objects.filter(pk=generation.pk).update(actual_cost_rub=actual)
        generation.actual_cost_rub = actual

    if actual > 0:
        payload["cost_rub"] = str(actual)
        payload["message"] = (
            "Запрос прервался после подтверждённого расхода LLM. "
            f"Списана только подтверждённая стоимость {actual} ₽; остаток резерва возвращён."
        )
    else:
        payload["cost_rub"] = "0"
        payload["message"] = PROVIDER_ERROR_MESSAGES.get(
            code,
            "Запрос прервался до подтверждения расхода LLM. Деньги не списаны.",
        )
    return f"event: error\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _model_is_internal(slug):
    slug = str(slug or "")
    if not slug:
        return False
    if slug.casefold().startswith("gigachat"):
        return True
    return AIModel.objects.filter(slug=slug, provider__slug="gigachat").exists()


def _generation_route_is_internal(generation):
    if str(generation.provider_slug or "").casefold() == "gigachat":
        return True
    if _model_is_internal(generation.model):
        return True
    try:
        selected = generation.routing_decision.selected_model
        return selected.provider.slug == "gigachat"
    except Exception:
        return False


def _publicize_sse_chunk(generation, chunk):
    if not isinstance(chunk, str) or not chunk.startswith("event: "):
        return chunk
    lines = chunk.splitlines()
    if not lines:
        return chunk
    event = lines[0][7:].strip() if lines[0].startswith("event: ") else ""
    if event not in {"routing", "completed", "recovery"}:
        return chunk
    try:
        data_line = next(line for line in lines if line.startswith("data: "))
        payload = json.loads(data_line[6:])
    except Exception:
        return chunk

    provider_internal = str(payload.get("provider") or "").casefold() == "gigachat"
    model_internal = _model_is_internal(payload.get("model"))
    from_model_internal = _model_is_internal(payload.get("from_model"))
    if event == "routing" and not model_internal:
        model_internal = _generation_route_is_internal(generation)
    if not (provider_internal or model_internal or from_model_internal):
        return chunk

    try:
        mode = generation.user_message.conversation.routing_mode
    except Exception:
        mode = "balanced"
    level = PUBLIC_SYSTEM_LEVELS.get(mode, "System Pro")
    if model_internal and "model" in payload:
        payload["model"] = level
    if model_internal and "model_version" in payload:
        payload["model_version"] = level
    if provider_internal:
        payload["provider"] = "system"
    if from_model_internal:
        payload["from_model"] = level
    if event == "routing" and "explanation" in payload and model_internal:
        payload["explanation"] = f"Использован уровень {level}."
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _provider_failure(code):
    value = str(code or "").casefold()
    return value in _PROVIDER_FAILURE_CODES or any(
        value.startswith(prefix) for prefix in _PROVIDER_FAILURE_PREFIXES
    )


def _required_capabilities(generation):
    try:
        return set(generation.routing_decision.required_capabilities or ["text"])
    except Exception:
        return {"text"}


def _emergency_candidates(generation):
    """Return verified healthy models outside the commercial route for continuity.

    The emergency path may absorb provider cost, but it never sends customer
    traffic to an UNKNOWN/OPEN provider or to a model missing runtime metadata.
    """
    attempted = set(generation.attempts.values_list("model_slug", flat=True))
    attempted_providers = set(
        generation.attempts.values_list("provider__slug", flat=True)
    )
    required = _required_capabilities(generation)
    rows = []
    queryset = (
        AIModel.objects.filter(enabled=True)
        .select_related("provider", "current_version")
        .order_by("provider__priority", "display_name")
    )
    for model in queryset:
        if model.slug in attempted:
            continue
        if not model.upstream_model.strip() or not model.current_version_id:
            continue
        if required - set(model.capabilities or ["text", "streaming"]):
            continue
        if not provider_available(model.provider):
            continue
        try:
            active_price(model.slug)
        except Exception:
            continue
        rows.append((model.provider.slug in attempted_providers, model))
    rows.sort(key=lambda item: item[0])
    return [model for _same_provider, model in rows]


def _record_emergency_cost(generation, model, completed):
    try:
        price = active_price(model.slug)
        provider_cost, _normal_charge = calculate(
            price,
            completed.input_tokens,
            completed.output_tokens,
        )
        request_cost = RequestCost.objects.filter(generation_id=generation.id).first()
        if request_cost is not None:
            request_cost.price_version = price
            request_cost.provider_cost_rub = provider_cost
            request_cost.charged_rub = 0
            request_cost.input_tokens = completed.input_tokens
            request_cost.output_tokens = completed.output_tokens
            request_cost.gross_profit_rub = -provider_cost
            request_cost.gross_margin_percent = -100
            request_cost.reconciliation_status = RequestCost.ReconciliationStatus.MANUAL_REVIEW
            request_cost.save(
                update_fields=[
                    "price_version",
                    "provider_cost_rub",
                    "charged_rub",
                    "input_tokens",
                    "output_tokens",
                    "gross_profit_rub",
                    "gross_margin_percent",
                    "reconciliation_status",
                ]
            )
    except Exception:
        logger.exception(
            "Emergency failover cost accounting failed generation_id=%s model=%s",
            generation.id,
            model.slug,
        )


def _complete_emergency_generation(generation, model, text, completed):
    assistant = generation.assistant_message
    assistant.content = text
    assistant.status = Message.Status.COMPLETED
    assistant.save(update_fields=["content", "status"])
    generation.state = Generation.State.COMPLETED
    generation.error_code = ""
    generation.provider_request_id = completed.provider_request_id
    generation.input_tokens = completed.input_tokens
    generation.output_tokens = completed.output_tokens
    generation.actual_cost_rub = 0
    generation.routed_model = model.slug
    generation.provider_slug = model.provider.slug
    generation.completed_at = timezone.now()
    generation.save(
        update_fields=[
            "state",
            "error_code",
            "provider_request_id",
            "input_tokens",
            "output_tokens",
            "actual_cost_rub",
            "routed_model",
            "provider_slug",
            "completed_at",
        ]
    )
    _record_emergency_cost(generation, model, completed)


def _emergency_failover(generation):
    """Try another verified model after the normal route failed before first token.

    Output is buffered until a provider finishes successfully, preventing fragments
    from multiple providers being mixed in one customer-visible answer.
    """
    assistant = generation.assistant_message
    assistant.refresh_from_db(fields=["content", "status"])
    if assistant.content:
        return False, []

    history = generation.context_snapshot.get("provider_messages") or [
        {"role": generation.user_message.role, "content": generation.user_message.content}
    ]
    chunks = []
    max_output = int(
        generation.context_snapshot.get("budget", {}).get("output_reserved") or 4096
    )
    max_output = max(256, min(8192, max_output))
    sequence = generation.attempts.count()

    for model in _emergency_candidates(generation)[:4]:
        sequence += 1
        attempt = GenerationAttempt.objects.create(
            generation=generation,
            provider=model.provider,
            model_slug=model.slug,
            sequence=sequence,
        )
        started = time.monotonic()
        text = ""
        completed = None
        provider_adapter = None
        try:
            provider_adapter = adapter_for(model)
            for event in provider_adapter.stream(
                model=model.upstream_model,
                messages=history,
                max_output_tokens=min(max_output, model.max_output_tokens),
            ):
                if event.kind == "delta":
                    text += event.text_delta
                else:
                    completed = event
            if not text.strip() or completed is None:
                raise ProviderError(
                    "Emergency provider returned no complete answer",
                    code="invalid_stream",
                    retryable=True,
                )
            latency = int((time.monotonic() - started) * 1000)
            attempt.state = GenerationAttempt.State.COMPLETED
            attempt.latency_ms = latency
            attempt.finished_at = timezone.now()
            attempt.save(update_fields=["state", "latency_ms", "finished_at"])
            record_success(model.provider, latency, adapter=provider_adapter)
            _complete_emergency_generation(generation, model, text, completed)
            chunks.append(
                "event: recovery\ndata: "
                + json.dumps(
                    {
                        "action": "emergency_fallback",
                        "message": "Основной AI-канал не ответил. Переключился на резервный канал без доплаты.",
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            step = 160
            for start in range(0, len(text), step):
                chunks.append(
                    "event: delta\ndata: "
                    + json.dumps({"text": text[start : start + step]}, ensure_ascii=False)
                    + "\n\n"
                )
            chunks.append(
                "event: completed\ndata: "
                + json.dumps(
                    {
                        "state": "completed",
                        "cost_rub": "0.0000",
                        "input_tokens": completed.input_tokens,
                        "output_tokens": completed.output_tokens,
                        "model": model.slug,
                        "model_version": model.current_version.version if model.current_version else None,
                        "provider": model.provider.slug,
                        "emergency_fallback": True,
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            return True, chunks
        except ProviderError as exc:
            attempt.state = GenerationAttempt.State.FAILED
            attempt.error_code = exc.code
            attempt.retryable = exc.retryable
            attempt.latency_ms = int((time.monotonic() - started) * 1000)
            attempt.finished_at = timezone.now()
            attempt.save(
                update_fields=[
                    "state",
                    "error_code",
                    "retryable",
                    "latency_ms",
                    "finished_at",
                ]
            )
            record_failure(model.provider, exc, adapter=provider_adapter)
        except Exception:
            logger.exception(
                "Emergency failover candidate crashed generation_id=%s model=%s",
                generation.id,
                model.slug,
            )
            attempt.state = GenerationAttempt.State.FAILED
            attempt.error_code = "emergency_internal_error"
            attempt.retryable = False
            attempt.latency_ms = int((time.monotonic() - started) * 1000)
            attempt.finished_at = timezone.now()
            attempt.save(
                update_fields=[
                    "state",
                    "error_code",
                    "retryable",
                    "latency_ms",
                    "finished_at",
                ]
            )
    return False, []


def managed_run(generation, *, adapter=None):
    """Wrap streaming with durable billing and a final cross-provider continuity layer."""
    try:
        for chunk in run(generation, adapter=adapter):
            payload = _parse_error_chunk(chunk)
            if (
                adapter is None
                and payload is not None
                and _provider_failure(payload.get("code"))
            ):
                recovered, recovery_chunks = _emergency_failover(generation)
                if recovered:
                    for recovery_chunk in recovery_chunks:
                        yield _publicize_sse_chunk(generation, recovery_chunk)
                    return
            chunk = _rewrite_error_chunk_if_needed(generation, chunk)
            yield _publicize_sse_chunk(generation, chunk)
    finally:
        _finalize_unhandled_disconnect(generation)
