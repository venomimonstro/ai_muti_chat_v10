from __future__ import annotations

import os
import sys
from decimal import Decimal, ROUND_UP

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.ai_registry.models import ProviderApiKey

from .models import ProviderFundingAccount, ProviderSpendReservation

ZERO = Decimal("0")
NATIVE_STEP = Decimal("0.000001")


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(default)


def account_available_native(account: ProviderFundingAccount) -> Decimal:
    return (
        account.funded_native - account.reserved_native - account.spent_native
    ).quantize(NATIVE_STEP)


def _allowed_key_states(*, allow_probe: bool) -> set[str]:
    states = {ProviderApiKey.HealthState.HEALTHY}
    if allow_probe:
        states.update(
            {
                ProviderApiKey.HealthState.UNKNOWN,
                ProviderApiKey.HealthState.DEGRADED,
            }
        )
    return states


def account_credential_ready(
    account: ProviderFundingAccount,
    *,
    allow_probe: bool = False,
) -> bool:
    """Return whether this exact funding account has a usable credential.

    Customer traffic accepts HEALTHY DB credentials only. Background recovery may
    additionally prove UNKNOWN/DEGRADED credentials. Environment credentials have no
    per-key health row, so provider-level health remains their guardrail.
    """
    if not account.active:
        return False
    if account.api_key_id:
        try:
            key = account.api_key
            return bool(
                key.enabled
                and key.health_state in _allowed_key_states(allow_probe=allow_probe)
                and key.get_secret()
            )
        except Exception:
            return False
    return bool(account.credential_env and os.getenv(account.credential_env, "").strip())


def account_matches(
    account: ProviderFundingAccount,
    *,
    required_native=ZERO,
    currency: str = "",
    allow_probe: bool = False,
    require_balance: bool = True,
) -> bool:
    required = max(ZERO, _d(required_native)).quantize(NATIVE_STEP, rounding=ROUND_UP)
    expected_currency = str(currency or "").upper().strip()
    if expected_currency and str(account.currency or "").upper().strip() != expected_currency:
        return False
    if not account_credential_ready(account, allow_probe=allow_probe):
        return False
    if require_balance:
        minimum = required if required > ZERO else NATIVE_STEP
        if account_available_native(account) < minimum:
            return False
    return True


def runtime_funding_accounts(
    provider,
    *,
    required_native=ZERO,
    currency: str = "",
    allow_probe: bool = False,
    require_balance: bool = True,
    lock: bool = False,
):
    """Return deterministic request-safe funding candidates.

    HEALTHY credentials are always preferred. The administrator's default account is
    the preferred account *inside the same health tier*, followed by explicit account
    priority. A depleted/broken default therefore cannot hide a paid healthy backup.
    """
    queryset = ProviderFundingAccount.objects.filter(provider=provider, active=True)
    if lock:
        queryset = queryset.select_for_update()
    accounts = list(queryset.select_related("api_key").order_by("priority", "created_at", "id"))

    def rank(account):
        if account.api_key_id:
            state = account.api_key.health_state
            health_rank = {
                ProviderApiKey.HealthState.HEALTHY: 0,
                ProviderApiKey.HealthState.UNKNOWN: 1,
                ProviderApiKey.HealthState.DEGRADED: 2,
            }.get(state, 9)
        else:
            health_rank = 0
        return (health_rank, 0 if account.is_default else 1, account.priority, account.created_at, str(account.id))

    accounts.sort(key=rank)
    return [
        account
        for account in accounts
        if account_matches(
            account,
            required_native=required_native,
            currency=currency,
            allow_probe=allow_probe,
            require_balance=require_balance,
        )
    ]


def select_runtime_funding_account(
    provider,
    *,
    required_native=ZERO,
    currency: str = "",
    allow_probe: bool = False,
    require_balance: bool = True,
):
    candidates = runtime_funding_accounts(
        provider,
        required_native=required_native,
        currency=currency,
        allow_probe=allow_probe,
        require_balance=require_balance,
    )
    return candidates[0] if candidates else None


def account_secret(account: ProviderFundingAccount) -> tuple[str, object | None]:
    if account.api_key_id:
        key = account.api_key
        return str(key.get_secret() or "").strip(), key.pk
    if account.credential_env:
        return os.getenv(account.credential_env, "").strip(), None
    return "", None


@transaction.atomic
def reserve_provider_spend(
    *,
    provider,
    amount_native,
    source_key,
    currency: str = "",
):
    """Reserve purchased capacity on one concrete healthy funding account.

    The provider row serializes account selection for this provider so two concurrent
    requests cannot both observe the same last units as free. The reservation is the
    durable identity later used by Chat to select the matching API credential.
    """
    from apps.ai_registry.models import Provider

    amount = _d(amount_native).quantize(NATIVE_STEP, rounding=ROUND_UP)
    if amount <= ZERO:
        return None
    existing = (
        ProviderSpendReservation.objects.select_related("account")
        .filter(source_key=source_key)
        .first()
    )
    if existing:
        return existing

    Provider.objects.select_for_update().only("pk").get(pk=provider.pk)
    existing = (
        ProviderSpendReservation.objects.select_related("account")
        .filter(source_key=source_key)
        .first()
    )
    if existing:
        return existing

    candidates = runtime_funding_accounts(
        provider,
        required_native=amount,
        currency=currency,
        allow_probe=False,
        require_balance=True,
        lock=True,
    )
    if not candidates:
        raise ValidationError(
            "Нет активного HEALTHY API-аккаунта с достаточным закупочным балансом"
        )
    account = candidates[0]
    account.reserved_native += amount
    account.save(update_fields=["reserved_native", "updated_at"])
    return ProviderSpendReservation.objects.create(
        account=account,
        amount_native=amount,
        source_key=source_key,
    )


def install(*, services_module, signals_module, reliability_module) -> None:
    """Install multi-account procurement without changing public service APIs."""
    services_module.reserve_provider_spend = reserve_provider_spend
    signals_module.reserve_provider_spend = reserve_provider_spend

    def procurement_ready(provider) -> bool:
        try:
            return select_runtime_funding_account(
                provider,
                required_native=NATIVE_STEP,
                allow_probe=False,
                require_balance=True,
            ) is not None
        except Exception:
            return False

    reliability_module._procurement_ready = procurement_ready

    # Modules that imported reserve_provider_spend by value before ProcurementConfig
    # ready() must follow the same account selector. Later imports naturally get the
    # patched service function.
    for module_name in (
        "apps.agents.accounting",
        "apps.procurement.chat_signals",
        "apps.procurement.recovery_signals",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "reserve_provider_spend"):
            module.reserve_provider_spend = reserve_provider_spend
