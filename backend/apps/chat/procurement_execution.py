from __future__ import annotations

from contextvars import ContextVar

from apps.ai_registry.models import Provider
from apps.billing.models import RequestCost
from apps.procurement.account_routing import account_credential_ready
from apps.procurement.models import ProviderSpendReservation


_generation_id: ContextVar[str | None] = ContextVar(
    "chat_procurement_execution_generation_id",
    default=None,
)


def _current_request_cost(generation_id):
    if not generation_id:
        return None
    return (
        RequestCost.objects.select_related("price_version")
        .filter(generation_id=generation_id)
        .first()
    )


def _request_provider_reservation(generation_id, provider):
    request_cost = _current_request_cost(generation_id)
    if request_cost is None:
        return None
    prefix = f"chat:{request_cost.id}:"
    return (
        ProviderSpendReservation.objects.filter(
            source_key__startswith=prefix,
            state=ProviderSpendReservation.State.ACTIVE,
            account__provider=provider,
        )
        .select_related("account", "account__api_key")
        .order_by("-created_at")
        .first()
    )


def _request_owns_provider_reservation(generation_id, provider) -> bool:
    return _request_provider_reservation(generation_id, provider) is not None


def _provider_execution_ready(provider: Provider, *, reservation=None) -> bool:
    if not provider.enabled or provider.emergency_disabled:
        return False
    if provider.adapter_type == Provider.AdapterType.ECHO and provider.slug not in {
        "gigachat",
        "openrouter",
    }:
        return True
    if provider.health_state not in {
        Provider.HealthState.HEALTHY,
        Provider.HealthState.DEGRADED,
    }:
        return False
    if reservation is not None:
        return account_credential_ready(reservation.account, allow_probe=False)
    from apps.ai_registry.dispatch import runtime_credential_ready

    return runtime_credential_ready(provider)


def install(streaming_module) -> None:
    """Bind one chat Generation to its exact purchased API account."""
    if getattr(streaming_module.run, "_ai_workspace_procurement_execution", False):
        return

    raw_run = streaming_module.run
    raw_provider_available = streaming_module.provider_available
    raw_snapshot_capacity = streaming_module._snapshot_capacity
    raw_adapter_for = getattr(streaming_module, "adapter_for", None)

    def provider_available(provider):
        generation_id = _generation_id.get()
        if generation_id:
            reservation = _request_provider_reservation(generation_id, provider)
            if reservation is not None:
                return _provider_execution_ready(provider, reservation=reservation)
        return raw_provider_available(provider)

    def snapshot_capacity(model, route_price):
        generation_id = _generation_id.get()
        if generation_id and _request_owns_provider_reservation(
            generation_id,
            model.provider,
        ):
            return True
        return raw_snapshot_capacity(model, route_price)

    def adapter_for(model, *args, **kwargs):
        generation_id = _generation_id.get()
        if generation_id:
            reservation = _request_provider_reservation(
                generation_id,
                model.provider,
            )
            if reservation is not None:
                kwargs["funding_account_id"] = reservation.account_id
        return raw_adapter_for(model, *args, **kwargs)

    def run(generation, *args, **kwargs):
        token = _generation_id.set(str(generation.id))
        try:
            yield from raw_run(generation, *args, **kwargs)
        finally:
            _generation_id.reset(token)

    provider_available._ai_workspace_procurement_execution = True
    provider_available._raw_provider_available = raw_provider_available
    snapshot_capacity._ai_workspace_procurement_execution = True
    snapshot_capacity._raw_snapshot_capacity = raw_snapshot_capacity
    run._ai_workspace_procurement_execution = True
    run._raw_run = raw_run

    streaming_module.provider_available = provider_available
    streaming_module._snapshot_capacity = snapshot_capacity
    if raw_adapter_for is not None:
        adapter_for._ai_workspace_procurement_execution = True
        adapter_for._raw_adapter_for = raw_adapter_for
        streaming_module.adapter_for = adapter_for
    streaming_module.run = run
