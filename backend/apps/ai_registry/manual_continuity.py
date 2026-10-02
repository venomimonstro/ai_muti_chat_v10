from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import AIModel


def _fallback_manual_route(router_module, policy, classification, conversation, input_tokens):
    """Build continuity from the first model that can actually serve this request.

    A stored manual choice can still exist in the database while being unusable for
    the current request: its provider/key may be down, the model may be quarantined,
    procurement capacity can be exhausted, or the request may require capabilities /
    context that model cannot provide. In all of those cases the unavailable model's
    historical price must not become the ceiling for the first working replacement.
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

    # The first actually eligible replacement is always allowed. Only *additional*
    # fallbacks are constrained by the price multiplier, so continuity cannot be
    # blocked merely because the unavailable requested model happened to be cheap.
    selected_row = eligible[0]
    baseline = Decimal(selected_row["estimated_cost_rub"])
    allowed = []
    for row in eligible:
        charge = Decimal(row["estimated_cost_rub"])
        row["fallback_allowed"] = charge <= baseline * multiplier or row is selected_row
        if row["fallback_allowed"]:
            row["rank"] = len(allowed) + 1
            allowed.append(row)
        else:
            row["status"] = "rejected"
            if "fallback_price_requires_consent" not in row["reasons"]:
                row["reasons"].append("fallback_price_requires_consent")

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
            f"Выбранная модель {requested} сейчас недоступна для этого запроса; "
            f"запрос автоматически направлен в {selected.display_name}."
        ),
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=min(router_module.OUTPUT_TOKENS, selected.max_output_tokens),
        estimated_cost_rub=Decimal(selected_row["estimated_cost_rub"]),
    )


def install(router_module) -> None:
    if getattr(router_module._manual_route, "_ai_workspace_manual_continuity", False) is True:
        return
    raw_manual_route = router_module._manual_route

    def manual_route(policy, classification, conversation, input_tokens):
        selected_slug = str(conversation.selected_model or "").strip()
        selected = (
            AIModel.objects.filter(slug=selected_slug, enabled=True)
            .select_related("provider", "current_version", "fallback_model")
            .first()
            if selected_slug
            else None
        )
        if selected is not None:
            thresholds = policy.thresholds or {}
            row = router_module._route_row(
                selected,
                classification,
                input_tokens,
                default_quality=float(thresholds.get("default_quality", 0.55)),
                unknown_latency=int(thresholds.get("unknown_latency_ms", 1500)),
            )
            if row["status"] == "eligible":
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
