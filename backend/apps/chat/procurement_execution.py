from __future__ import annotations

from contextvars import ContextVar

from apps.ai_registry.dispatch import runtime_credential_ready
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import RequestCost
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


def _request_owns_provider_reservation(generation_id, provider) -> bool:
    request_cost = _current_request_cost(generation_id)
    if request_cost is None:
        return False
    model = (
        AIModel.objects.filter(slug=request_cost.price_version.model_slug)
        .only("provider_id")
        .first()
    )
    if model is None or model.provider_id != provider.id:
        return False
    prefix = f"chat:{request_cost.id}:"
    return ProviderSpendReservation.objects.filter(
        source_key__startswith=prefix,
        state=ProviderSpendReservation.State.ACTIVE,
    ).exists()


def _provider_execution_ready(provider: Provider) -> bool:
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
    return runtime_credential_ready(provider)


def install(streaming_module) -> None:
    """Make the streaming execution phase aware of its own provider reservation.

    Preflight and a new provider must pass the normal free-balance procurement
    checks. Once this Generation owns an ACTIVE reservation for a provider, however,
    a fallback model on that same provider must be allowed to reach the atomic
    RequestCost switch. The procurement signal releases the old model reservation and
    reserves the replacement inside the same transaction; if the replacement really
    costs too much, that save fails and rolls back before any provider call.

    This avoids a false-negative where the primary model's own reservation made the
    account look empty and incorrectly blocked a cheaper/equally funded sibling model.
    Reservations belonging to another Generation never count here.
    """
    if getattr(streaming_module.run, "_ai_workspace_procurement_execution", False):
        return

    raw_run = streaming_module.run
    raw_provider_available = streaming_module.provider_available
    raw_snapshot_capacity = streaming_module._snapshot_capacity

    def provider_available(provider):
        generation_id = _generation_id.get()
        if generation_id and _request_owns_provider_reservation(generation_id, provider):
            return _provider_execution_ready(provider)
        return raw_provider_available(provider)

    def snapshot_capacity(model, route_price):
        generation_id = _generation_id.get()
        # Do not require the fallback PriceVersion to equal the currently reserved
        # PriceVersion. That equality is impossible before the atomic switch and was
        # the source of same-provider fallback failures. request_cost.save() remains
        # the authoritative amount check before adapter_for()/provider traffic.
        if generation_id and _request_owns_provider_reservation(
            generation_id,
            model.provider,
        ):
            return True
        return raw_snapshot_capacity(model, route_price)

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
    streaming_module.run = run
