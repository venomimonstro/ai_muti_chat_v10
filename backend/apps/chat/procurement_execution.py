from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal

from django.db import transaction

from apps.ai_registry.models import Provider
from apps.billing.models import RequestCost
from apps.procurement.account_routing import account_credential_ready, runtime_funding_accounts
from apps.procurement.models import ProviderFundingAccount, ProviderSpendReservation


_generation_id: ContextVar[str | None] = ContextVar(
    "chat_procurement_execution_generation_id",
    default=None,
)

# These failures describe the exact commercial execution candidate, not the
# upstream provider as a whole. They must never degrade another healthy key when
# adapter construction fails before a concrete credential identity is available.
CANDIDATE_SCOPED_FAILURE_CODES = {
    "candidate_not_ready",
    "provider_funding_unavailable",
}


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


@transaction.atomic
def _rebind_failed_provider_reservation(generation_id, provider) -> bool:
    """Move an active pre-usage reserve from a failed key to a healthy sibling.

    ``ProviderSpendReservation.source_key`` is unique and represents one immutable
    commercial operation. Creating a second reservation for the same RequestCost is
    therefore intentionally impossible. Before any provider usage is confirmed it is
    safe to move that *active* reservation to another funding account while preserving
    source identity and amount. Both account balances and the reservation row are
    locked so concurrent retries cannot reserve the same capacity twice.
    """
    if not generation_id:
        return False
    request_cost = _current_request_cost(generation_id)
    if request_cost is None or request_cost.provider_cost_rub is not None:
        return False
    prefix = f"chat:{request_cost.id}:"
    reservation = (
        ProviderSpendReservation.objects.select_for_update()
        .select_related("account", "account__api_key")
        .filter(
            source_key__startswith=prefix,
            state=ProviderSpendReservation.State.ACTIVE,
            account__provider=provider,
        )
        .order_by("-created_at")
        .first()
    )
    if reservation is None:
        return False
    if account_credential_ready(reservation.account, allow_probe=False):
        return False

    old_account_id = reservation.account_id
    amount = reservation.amount_native
    currency = str(reservation.account.currency or "").upper().strip()
    # lock=True locks every active funding-account row for this provider before the
    # in-Python readiness filter is applied, including the failed old account.
    candidates = runtime_funding_accounts(
        provider,
        required_native=amount,
        currency=currency,
        allow_probe=False,
        require_balance=True,
        lock=True,
    )
    replacement = next(
        (account for account in candidates if account.id != old_account_id),
        None,
    )
    if replacement is None:
        return False

    old_account = ProviderFundingAccount.objects.get(pk=old_account_id)
    replacement = ProviderFundingAccount.objects.get(pk=replacement.pk)
    old_account.reserved_native = max(
        Decimal("0"), old_account.reserved_native - amount
    )
    replacement.reserved_native += amount
    old_account.save(update_fields=["reserved_native", "updated_at"])
    replacement.save(update_fields=["reserved_native", "updated_at"])
    reservation.account = replacement
    reservation.save(update_fields=["account"])
    return True


def install(streaming_module) -> None:
    """Bind one chat Generation to its exact purchased API account."""
    if getattr(streaming_module.run, "_ai_workspace_procurement_execution", False):
        return

    raw_run = streaming_module.run
    raw_provider_available = streaming_module.provider_available
    raw_snapshot_capacity = streaming_module._snapshot_capacity
    raw_adapter_for = getattr(streaming_module, "adapter_for", None)
    raw_record_failure = getattr(streaming_module, "record_failure", None)

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

    def record_failure(provider, error, adapter=None):
        code = str(getattr(error, "code", "") or "").strip().casefold()
        if adapter is None and code in CANDIDATE_SCOPED_FAILURE_CODES:
            # The reserved account/credential changed after preflight. There is no
            # verified failing provider credential to attribute this to, so mutating
            # provider/key health would poison unrelated customer traffic. The normal
            # stream loop records the GenerationAttempt and continues to fallback.
            return None
        result = raw_record_failure(provider, error, adapter=adapter)
        # record_failure attributes the error to the exact adapter/key first. If that
        # makes the reserved credential non-routable and provider usage has not been
        # confirmed, atomically move the existing reserve to the next HEALTHY funded
        # account. A non-retryable auth/quota error applies to the failed credential;
        # after a successful rebind one same-model retry through the new credential is
        # safe and desirable.
        rebound = False
        if adapter is not None:
            try:
                rebound = _rebind_failed_provider_reservation(
                    _generation_id.get(), provider
                )
            except Exception:
                # Rebinding is an availability optimization, never a reason to hide
                # the original provider failure or corrupt its health attribution.
                rebound = False
        if rebound and hasattr(error, "retryable"):
            error.retryable = True
        return result

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
    if raw_record_failure is not None:
        record_failure._ai_workspace_procurement_execution = True
        record_failure._raw_record_failure = raw_record_failure
        streaming_module.record_failure = record_failure
    streaming_module.run = run
