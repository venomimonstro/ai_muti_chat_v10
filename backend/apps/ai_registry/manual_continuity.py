from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import AIModel


def _fallback_manual_route(router_module, policy, classification, conversation, input_tokens):
    """Build a safe continuity route when the explicitly selected model vanished.

    This path is intentionally narrow: it runs only when the stored manual model is
    missing or administratively disabled. Provider outages for an existing model are
    already handled by router._manual_route itself. We keep manual continuity outside
    AUTO tier pools because an unavailable explicit choice must not make the chat
    unusable merely because the classified tier is empty.
    """
    thresholds = policy.thresholds or {}
    default_quality = float(thresholds.get("default_quality", 0.55))
    unknown_latency = int(thresholds.get("unknown_latency_ms", 1500))
    multiplier = Decimal(str(thresholds.get("fallback_price_multiplier", 1.5)))

    ordered = list(
        AIModel.objects.filter(enabled=True)
        .select_related("provider", "current_version", "fallback_model")
        .order_by("provider__priority", "display_name", "slug")
    )
    rows = [
        router_module._route_row(
            model,
            classification,
            input_tokens,
            default_quality=default_quality,
            unknown_latency=unknown_latency,
        )
        for model in ordered
    ]
    eligible = [row for row in rows if row["status"] == "eligible"]
    if not eligible:
        raise ValidationError("Нет доступной модели для выполнения запроса")

    # Prefer a model that can actually satisfy the current request; provider
    # priority provides deterministic continuity. The selected fallback becomes
    # the cost baseline so additional failover candidates cannot unexpectedly
    # exceed the configured multiplier.
    selected_row = eligible[0]
    baseline = Decimal(selected_row["estimated_cost_rub"])
    allowed = []
    for row in eligible:
        charge = Decimal(row["estimated_cost_rub"])
        row["fallback_allowed"] = charge <= baseline * multiplier or row is selected_row
        if row["fallback_allowed"]:
            row["rank"] = len(allowed) + 1
            allowed.append(row)

    lookup = {model.slug: model for model in ordered}
    selected = lookup[selected_row["model"]]
    requested = str(conversation.selected_model or "").strip() or "ранее выбранная модель"
    return router_module.RouteSelection(
        policy=policy,
        classification=classification,
        selected=selected,
        ordered_models=[lookup[row["model"]] for row in allowed],
        candidates=rows,
        explanation=(
            f"Выбранная модель {requested} сейчас отключена или недоступна; "
            f"запрос автоматически направлен в {selected.display_name}."
        ),
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=min(router_module.OUTPUT_TOKENS, selected.max_output_tokens),
        estimated_cost_rub=Decimal(selected_row["estimated_cost_rub"]),
    )


def install(router_module) -> None:
    if getattr(router_module._manual_route, "_ai_workspace_manual_continuity", False):
        return
    raw_manual_route = router_module._manual_route

    def manual_route(policy, classification, conversation, input_tokens):
        selected_slug = str(conversation.selected_model or "").strip()
        exists_and_enabled = bool(
            selected_slug
            and AIModel.objects.filter(slug=selected_slug, enabled=True).exists()
        )
        if exists_and_enabled:
            return raw_manual_route(policy, classification, conversation, input_tokens)
        return _fallback_manual_route(
            router_module,
            policy,
            classification,
            conversation,
            input_tokens,
        )

    manual_route._ai_workspace_manual_continuity = True
    manual_route._raw_manual_route = raw_manual_route
    router_module._manual_route = manual_route
