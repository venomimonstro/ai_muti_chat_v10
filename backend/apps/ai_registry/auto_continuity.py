from __future__ import annotations

import sys

from django.core.exceptions import ValidationError


AUTO_FALLBACK_TIERS = {
    "economy": ("balanced", "maximum"),
    "balanced": ("maximum", "economy"),
    "maximum": ("balanced", "economy"),
}


def _availability_failure(exc: ValidationError) -> bool:
    text = " ".join(str(item) for item in getattr(exc, "messages", [str(exc)])).casefold()
    return "нет доступных моделей" in text or "не настроил модели" in text


def install(router_module) -> None:
    """Allow AUTO to survive a complete outage of its preferred tier.

    Explicit Simple/Medium/Complex modes remain strict. AUTO first uses the tier
    selected by task classification; only if that whole pool has no usable model
    does it cross into the nearest continuity tier. The wrapper does not persist a
    different conversation mode and therefore preserves the user's AUTO choice.
    """
    if getattr(router_module.select_route, "_ai_workspace_auto_continuity", False):
        return
    raw_select_route = router_module.select_route

    def select_route(*, conversation, content):
        requested_mode = conversation.routing_mode
        if requested_mode != "auto":
            return raw_select_route(conversation=conversation, content=content)

        try:
            return raw_select_route(conversation=conversation, content=content)
        except ValidationError as primary_error:
            if not _availability_failure(primary_error):
                raise

        classification = router_module.classify_task(content, conversation)
        preferred_tier = router_module._auto_tier(classification)
        fallback_errors = []
        original_mode = conversation.routing_mode
        try:
            for tier in AUTO_FALLBACK_TIERS.get(preferred_tier, ("balanced", "maximum", "economy")):
                conversation.routing_mode = tier
                try:
                    route = raw_select_route(conversation=conversation, content=content)
                except ValidationError as exc:
                    if not _availability_failure(exc):
                        raise
                    fallback_errors.append(str(exc))
                    continue
                preferred_label = router_module.MODE_LABELS.get(preferred_tier, preferred_tier)
                fallback_label = router_module.MODE_LABELS.get(tier, tier)
                return router_module.RouteSelection(
                    policy=route.policy,
                    classification=route.classification,
                    selected=route.selected,
                    ordered_models=route.ordered_models,
                    candidates=route.candidates,
                    explanation=(
                        f"AUTO определил уровень «{preferred_label}», но его модели временно недоступны. "
                        f"Для непрерывности использован резервный уровень «{fallback_label}» и "
                        f"модель {route.selected.display_name}."
                    ),
                    estimated_input_tokens=route.estimated_input_tokens,
                    estimated_output_tokens=route.estimated_output_tokens,
                    estimated_cost_rub=route.estimated_cost_rub,
                )
        finally:
            conversation.routing_mode = original_mode

        raise primary_error

    select_route._ai_workspace_auto_continuity = True
    select_route._raw_select_route = raw_select_route
    router_module.select_route = select_route

    # Some chat modules import select_route by value. Rebind only if they were
    # already imported before AppConfig.ready(); modules imported later naturally
    # receive the wrapped function.
    for name in ("apps.chat.streaming", "apps.chat.cost_preview"):
        module = sys.modules.get(name)
        if module is not None:
            module.select_route = select_route
