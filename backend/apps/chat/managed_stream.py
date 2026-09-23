import json
import logging

from django.utils import timezone

from apps.ai_registry.models import AIModel
from apps.billing.models import BalanceReservation
from apps.billing.services import release

from .models import Generation, Message
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


def _rewrite_error_chunk_if_needed(generation, chunk):
    """Keep the SSE message consistent with billing and expose actionable provider failures."""
    if not isinstance(chunk, str) or not chunk.startswith("event: error\n"):
        return chunk
    try:
        data_line = next(
            line for line in chunk.splitlines() if line.startswith("data: ")
        )
        payload = json.loads(data_line[6:])
    except Exception:
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
    """Never expose the internal GigaChat provider/model in customer chat SSE."""
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


def managed_run(generation, *, adapter=None):
    """Wrap the entire streaming lifecycle so disconnects and error billing stay durable."""
    try:
        for chunk in run(generation, adapter=adapter):
            chunk = _rewrite_error_chunk_if_needed(generation, chunk)
            yield _publicize_sse_chunk(generation, chunk)
    finally:
        _finalize_unhandled_disconnect(generation)
