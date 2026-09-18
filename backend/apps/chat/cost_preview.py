from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.ai_registry.router import select_route
from apps.billing.pricing import active_price, quote, require_margin

from .attachments import resolve_chat_attachments

VISION_RESERVE_TOKENS_PER_IMAGE = 2048


def _context_overhead_tokens(conversation):
    """Upper-bound non-prompt context that prepare() may add before calling a provider."""
    total = int(getattr(settings, "SMART_CONTEXT_OLD_MESSAGE_TOKENS", 1200))
    total += int(getattr(settings, "SMART_CONTEXT_SUMMARY_TOKENS", 1200))
    if conversation.project_id:
        total += int(getattr(settings, "SMART_CONTEXT_PROJECT_TOKENS", 2000))
        total += int(getattr(settings, "SMART_CONTEXT_FILE_TOKENS", 2400))
    if conversation.memory_enabled:
        total += int(getattr(settings, "SMART_CONTEXT_MEMORY_TOKENS", 1600))
    # A small bounded allowance for system/tool metadata and citations.
    total += 512
    return total


def chat_cost_preview(*, user, conversation, content, file_ids=None):
    if not isinstance(content, str) or not content.strip() or len(content) > 100_000:
        raise ValidationError("Сообщение должно содержать от 1 до 100000 символов")
    attachments, vision_assets = resolve_chat_attachments(
        user=user,
        conversation=conversation,
        file_ids=file_ids or [],
    )
    routing_content = content
    if vision_assets:
        routing_content += "\n[vision attachment: изображение фото скриншот]"
    if attachments and len(attachments) != len(vision_assets):
        routing_content += "\n[document attachment: файл документ таблица PDF]"
    route = select_route(conversation=conversation, content=routing_content)
    candidates = route.ordered_models
    if vision_assets:
        candidates = [item for item in candidates if "vision" in set(item.capabilities or [])]
    if not candidates:
        raise ValidationError("Нет доступной модели для этого запроса")

    extra_input = _context_overhead_tokens(conversation)
    extra_input += len(vision_assets) * VISION_RESERVE_TOKENS_PER_IMAGE
    rows = []
    maximum = Decimal("0")
    minimum = None
    for model in candidates:
        output_tokens = min(1024, model.max_output_tokens)
        max_input = max(32, route.estimated_input_tokens + extra_input)
        max_input = min(max_input, max(32, model.context_window - output_tokens - 32))
        value = require_margin(
            quote(
                active_price(model.slug),
                max_input,
                output_tokens,
                provider_slug=model.provider.slug,
                model_slug=model.slug,
            )
        )
        charge = value.user_charge_rub
        maximum = max(maximum, charge)
        minimum = charge if minimum is None else min(minimum, charge)
        rows.append(
            {
                "model": model.slug,
                "display_name": model.display_name,
                "estimated_max_rub": str(charge),
            }
        )

    threshold = Decimal(str(getattr(settings, "CHAT_CONFIRM_THRESHOLD_RUB", "20.00")))
    return {
        "estimated_min_rub": minimum or Decimal("0"),
        "estimated_max_rub": maximum,
        "confirmation_required": maximum >= threshold,
        "confirmation_threshold_rub": threshold,
        "selected_model": route.selected.slug,
        "models": rows,
    }
