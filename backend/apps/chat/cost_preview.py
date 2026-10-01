import os
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.accounts.services import spend_guard_snapshot
from apps.ai_registry.router import select_route
from apps.ai_registry.token_estimator import estimate_message_tokens
from apps.billing.models import Wallet
from apps.billing.pricing import active_price, quote, require_margin
from apps.procurement.readiness import quote_has_procurement_capacity

from .attachments import resolve_chat_attachments
from .models import Conversation, Message
from .paid_search_billing import expected_search_charge

MAX_OUTPUT_TOKENS = max(512, min(8192, int(os.getenv("CHAT_MAX_OUTPUT_TOKENS", "4096"))))
VISION_RESERVE_TOKENS_PER_IMAGE = 2048
PUBLIC_SYSTEM_LEVELS = {
    Conversation.RoutingMode.ECONOMY: "System Lite",
    Conversation.RoutingMode.BALANCED: "System Pro",
    Conversation.RoutingMode.MAXIMUM: "System Max",
}


def _actual_system_level(model, conversation):
    identity = f"{model.slug} {model.display_name} {model.upstream_model}".casefold()
    if "max" in identity:
        return "System Max"
    if "pro" in identity:
        return "System Pro"
    if "lite" in identity or "gigachat-2" in identity or "gigachat" in identity:
        return "System Lite"
    return PUBLIC_SYSTEM_LEVELS.get(conversation.routing_mode, "System Pro")


def _public_model(model, conversation):
    if model.provider.slug == "gigachat":
        level = _actual_system_level(model, conversation)
        return level, level
    return model.slug, model.display_name


def _context_overhead_tokens(conversation):
    """Upper-bound non-recent context that prepare() may add before provider execution.

    ``route.estimated_input_tokens`` already includes the current prompt and recent
    conversation history. Only context that the router does *not* account for belongs
    here: rolling summary/older history, project retrieval, memory and a small system
    envelope. Keeping this boundary explicit prevents preview from charging the same
    recent messages twice.
    """
    total = int(getattr(settings, "SMART_CONTEXT_OLD_MESSAGE_TOKENS", 1200))
    total += int(getattr(settings, "SMART_CONTEXT_SUMMARY_TOKENS", 1200))
    if conversation.project_id:
        total += int(getattr(settings, "SMART_CONTEXT_PROJECT_TOKENS", 2000))
        total += int(getattr(settings, "SMART_CONTEXT_FILE_TOKENS", 2400))
    if conversation.memory_enabled:
        total += int(getattr(settings, "SMART_CONTEXT_MEMORY_TOKENS", 1600))
    total += 512
    return total


def _existing_history_tokens(conversation):
    """Return the bounded recent-history footprint for diagnostics/tests.

    The router already includes this recent window in ``estimated_input_tokens``.
    This helper remains useful for observability and regression tests, but its result
    must not be added to the preview quote a second time.
    """
    recent_turns = max(1, int(getattr(settings, "SMART_CONTEXT_RECENT_TURNS", 6)))
    recent_message_limit = recent_turns * 2
    rows = list(
        Message.objects.filter(conversation=conversation)
        .exclude(status=Message.Status.FAILED)
        .order_by("-created_at")
        .values("role", "content")[:recent_message_limit]
    )
    rows.reverse()
    messages = [{"role": item["role"], "content": item["content"] or ""} for item in rows]
    return estimate_message_tokens(messages) if messages else 0


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
    llm_maximum = Decimal("0")
    minimum = None
    selected_model = None
    for model in candidates:
        output_tokens = min(MAX_OUTPUT_TOKENS, model.max_output_tokens)
        max_input = max(32, route.estimated_input_tokens + extra_input)
        max_input = min(max_input, max(32, model.context_window - output_tokens - 32))
        try:
            value = require_margin(
                quote(
                    active_price(model.slug),
                    max_input,
                    output_tokens,
                    provider_slug=model.provider.slug,
                    model_slug=model.slug,
                )
            )
        except ValidationError:
            continue
        if not quote_has_procurement_capacity(model.provider, value):
            continue
        if selected_model is None:
            selected_model = model
        charge = value.user_charge_rub
        llm_maximum = max(llm_maximum, charge)
        minimum = charge if minimum is None else min(minimum, charge)
        public_slug, public_name = _public_model(model, conversation)
        rows.append(
            {
                "model": public_slug,
                "display_name": public_name,
                "estimated_max_rub": str(charge),
            }
        )

    if selected_model is None or not rows:
        raise ValidationError("Сейчас нет модели с доступным API-балансом для этого запроса")

    # SearXNG is free and remains the normal first search path. The paid Yandex call
    # is therefore an upper-bound tool cost, not a guaranteed/minimum charge. Include
    # it in confirmation/spend-guard so the exact customer ceiling is never exceeded.
    search_maximum = expected_search_charge(content)
    maximum = llm_maximum + search_maximum
    threshold = Decimal(str(getattr(settings, "CHAT_CONFIRM_THRESHOLD_RUB", "20.00")))
    wallet, _ = Wallet.objects.get_or_create(user=user)
    guard = spend_guard_snapshot(wallet)
    single_limit = guard["single_request_limit_rub"]
    blocked = single_limit is not None and maximum > single_limit
    selected_slug, _selected_name = _public_model(selected_model, conversation)
    return {
        "estimated_min_rub": minimum or Decimal("0"),
        "estimated_max_rub": maximum,
        "estimated_llm_max_rub": llm_maximum,
        "estimated_search_max_rub": search_maximum,
        "confirmation_required": maximum >= threshold,
        "confirmation_threshold_rub": threshold,
        "selected_model": selected_slug,
        "models": rows,
        "spend_guard": {
            "single_request_limit_rub": single_limit,
            "single_request_balance_percent": guard["single_request_balance_percent"],
            "burst_limit_rub": guard["burst_limit_rub"],
            "burst_window_minutes": guard["burst_window_minutes"],
            "daily_limit_rub": guard["daily_system_limit_rub"],
        },
        "blocked_by_spend_guard": blocked,
        "spend_guard_message": (
            f"Расчётный максимум {maximum:.2f} ₽ выше защитного лимита {single_limit:.2f} ₽. "
            "Запрос не будет отправлен провайдеру и деньги не будут списаны."
            if blocked and single_limit is not None
            else ""
        ),
    }
