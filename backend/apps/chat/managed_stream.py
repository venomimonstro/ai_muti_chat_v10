import json
import logging

from django.utils import timezone

from apps.ai_registry.models import AIModel
from apps.billing.models import BalanceReservation
from apps.billing.services import release

from .models import Generation, Message
from .partial_billing import settle_delivered_partial
from .pipeline_trace import trace as pipeline_trace
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


def _reservation_actual(generation):
    if not generation.reservation_id:
        return None
    return BalanceReservation.objects.filter(pk=generation.reservation_id).values_list(
        "actual_rub", flat=True
    ).first()


def _sync_terminal_actual(generation):
    actual = _reservation_actual(generation)
    if actual is not None and generation.actual_cost_rub != actual:
        Generation.objects.filter(pk=generation.pk).update(actual_cost_rub=actual)
        generation.actual_cost_rub = actual
    return actual


def _finalize_active_stream(generation, *, state, error_code, log_label):
    """Close a still-active stream without losing confirmed provider usage."""
    generation.refresh_from_db(fields=["state", "reservation_id", "actual_cost_rub"])
    if generation.state not in {Generation.State.QUEUED, Generation.State.RUNNING}:
        _sync_terminal_actual(generation)
        return
    assistant = generation.assistant_message
    assistant.refresh_from_db(fields=["content", "status"])
    try:
        charge = settle_delivered_partial(generation, assistant.content)
    except Exception:
        logger.exception(
            "Managed stream %s settlement failed generation_id=%s",
            log_label,
            generation.id,
        )
        try:
            if generation.reservation_id:
                closed = release(generation.reservation_id)
                charge = closed.actual_rub or 0
            else:
                charge = 0
        except Exception:
            logger.exception(
                "Managed stream %s reservation release failed generation_id=%s",
                log_label,
                generation.id,
            )
            charge = generation.actual_cost_rub or 0
    assistant.status = Message.Status.PARTIAL if assistant.content else Message.Status.FAILED
    assistant.save(update_fields=["status"])
    generation.state = state
    generation.error_code = error_code
    generation.actual_cost_rub = charge
    generation.completed_at = timezone.now()
    generation.save(
        update_fields=["state", "error_code", "actual_cost_rub", "completed_at"]
    )


def _finalize_unhandled_disconnect(generation):
    _finalize_active_stream(
        generation,
        state=Generation.State.CANCELLED,
        error_code="client_cancelled",
        log_label="client-disconnect",
    )


def _finalize_unhandled_failure(generation):
    _finalize_active_stream(
        generation,
        state=Generation.State.FAILED,
        error_code="stream_runtime_failed",
        log_label="runtime-failure",
    )


def _finalize_incomplete_stream(generation):
    _finalize_active_stream(
        generation,
        state=Generation.State.FAILED,
        error_code="stream_incomplete",
        log_label="incomplete-return",
    )


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
    if str(getattr(generation, "provider_slug", "") or "").casefold() == "gigachat":
        return True
    if _model_is_internal(
        getattr(generation, "routed_model", "") or getattr(generation, "model", "")
    ):
        return True
    try:
        selected = generation.routing_decision.selected_model
        return selected.provider.slug == "gigachat"
    except Exception:
        return False


def _public_system_level(model_slug, *, fallback_mode="balanced"):
    slug = str(model_slug or "").strip()
    haystack = slug.casefold()

    # The common SSE path already carries enough public identity in the model slug.
    # Resolve it without a database round-trip; the fallback lookup is only needed
    # for an internally branded model whose slug itself is opaque.
    if "max" in haystack:
        return "System Max"
    if "pro" in haystack:
        return "System Pro"
    if "lite" in haystack or "gigachat-2" in haystack or "gigachat" in haystack:
        return "System Lite"

    if slug:
        model = (
            AIModel.objects.filter(slug=slug)
            .only("slug", "display_name", "upstream_model")
            .first()
        )
        if model is not None:
            haystack = f"{model.slug} {model.display_name} {model.upstream_model}".casefold()
            if "max" in haystack:
                return "System Max"
            if "pro" in haystack:
                return "System Pro"
            if "lite" in haystack or "gigachat-2" in haystack or "gigachat" in haystack:
                return "System Lite"
    return PUBLIC_SYSTEM_LEVELS.get(str(fallback_mode or ""), "System Pro")


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
    model_slug = (
        payload.get("model")
        or getattr(generation, "routed_model", "")
        or getattr(generation, "model", "")
    )
    from_model_slug = payload.get("from_model")
    # If the event already declares the internal provider, do not perform redundant
    # database identity lookups in the latency-sensitive SSE loop.
    model_internal = provider_internal or _model_is_internal(model_slug)
    from_model_internal = bool(from_model_slug) and (
        provider_internal or _model_is_internal(from_model_slug)
    )
    if event == "routing" and not model_internal:
        model_internal = _generation_route_is_internal(generation)

    if not (provider_internal or model_internal or from_model_internal):
        return chunk

    try:
        mode = generation.user_message.conversation.routing_mode
    except Exception:
        mode = "balanced"
    level = _public_system_level(model_slug, fallback_mode=mode)
    if model_internal and "model" in payload:
        payload["model"] = level
    if model_internal and "model_version" in payload:
        payload["model_version"] = level
    if provider_internal:
        payload["provider"] = "system"
    if from_model_internal:
        payload["from_model"] = _public_system_level(from_model_slug, fallback_mode=mode)
    if event == "routing" and "explanation" in payload and model_internal:
        payload["explanation"] = f"Использован уровень {level}."
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def managed_run(generation, *, adapter=None):
    passive_follower = False
    pipeline_trace("MANAGED_STREAM_START", generation=generation)
    try:
        for chunk in run(generation, adapter=adapter):
            payload = _parse_error_chunk(chunk)
            if payload and str(payload.get("code") or "") == "generation_in_progress":
                # Another request already owns this Generation. This stream is only
                # a follower and must never terminalize or settle the producer's work.
                passive_follower = True
            chunk = _rewrite_error_chunk_if_needed(generation, chunk)
            public_chunk = _publicize_sse_chunk(generation, chunk)
            if isinstance(public_chunk, str) and public_chunk.startswith("event: "):
                event_name = public_chunk.splitlines()[0][7:].strip()
                if event_name in {"completed", "error", "cancelled", "snapshot"}:
                    pipeline_trace(
                        "MANAGED_STREAM_EVENT",
                        generation=generation,
                        event=event_name,
                    )
            yield public_chunk
    except GeneratorExit:
        pipeline_trace("MANAGED_STREAM_DISCONNECT", generation=generation)
        if not passive_follower:
            _finalize_unhandled_disconnect(generation)
        raise
    except BaseException as exc:
        pipeline_trace(
            "MANAGED_STREAM_EXCEPTION",
            generation=generation,
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        if not passive_follower:
            _finalize_unhandled_failure(generation)
        raise
    else:
        if not passive_follower:
            _finalize_incomplete_stream(generation)
