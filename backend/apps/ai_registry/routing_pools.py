from __future__ import annotations

from .models import RoutingTierAssignment


VALID_TIERS = {
    RoutingTierAssignment.Tier.SIMPLE,
    RoutingTierAssignment.Tier.MEDIUM,
    RoutingTierAssignment.Tier.COMPLEX,
}


def _legacy_pool(thresholds: dict, tier: str) -> list[str]:
    raw = (thresholds.get("tier_models") or {}).get(tier)
    if isinstance(raw, str):
        value = raw.strip()
        return [value] if value else []
    if isinstance(raw, (list, tuple)):
        return [str(value).strip() for value in raw if str(value).strip()]
    return []


def database_tiers_configured() -> bool:
    """Return whether the operator has started using explicit DB-managed pools.

    As soon as at least one enabled assignment exists, the database becomes the
    authoritative source for every tier. This prevents stale JSON policy values
    from silently overriding what an administrator configured in the UI.
    """
    return RoutingTierAssignment.objects.filter(enabled=True).exists()


def tier_pool(thresholds: dict, tier: str) -> list[str]:
    if tier not in VALID_TIERS:
        return []
    if database_tiers_configured():
        return list(
            RoutingTierAssignment.objects.filter(
                tier=tier,
                enabled=True,
                model__enabled=True,
            )
            .order_by("priority", "model__provider__priority", "model__display_name", "model__slug")
            .values_list("model__slug", flat=True)
        )
    return _legacy_pool(thresholds, tier)


def tier_configuration_present(thresholds: dict) -> bool:
    if database_tiers_configured():
        return True
    raw = thresholds.get("tier_models")
    return isinstance(raw, dict) and any(_legacy_pool(thresholds, tier) for tier in VALID_TIERS)


def install_router_pool_resolver(router_module) -> None:
    """Install one pool resolver for AUTO and explicit complexity modes.

    Kept behind a tiny compatibility hook so old RoutingPolicyVersion rows remain
    readable while new installations are fully controlled from RoutingTierAssignment.
    """
    router_module._tier_pool = tier_pool
    router_module._tier_configuration_present = tier_configuration_present
