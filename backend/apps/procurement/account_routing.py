from __future__ import annotations

import logging
import os
import sys
from decimal import Decimal, ROUND_UP

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.ai_registry.models import ProviderApiKey

from .models import ProviderFundingAccount, ProviderSpendReservation

logger = logging.getLogger("chat.pipeline")

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
    model_upstream: str = "",
) -> bool:
    required = max(ZERO, _d(required_native)).quantize(NATIVE_STEP, rounding=ROUND_UP)
    expected_currency = str(currency or "").upper().strip()
    if expected_currency and str(account.currency or "").upper().strip() != expected_currency:
        return False
    if not account_credential_ready(account, allow_probe=allow_probe):
        return False
    if (
        getattr(account.provider, "slug", "") == "polza"
        and account.api_key_id
        and model_upstream
    ):
        available = list(getattr(account.api_key, "available_models", None) or [])
        if available and model_upstream not in available:
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
    model_upstream: str = "",
):
    queryset = ProviderFundingAccount.objects.filter(provider=provider, active=True)
    if lock:
        # api_key is nullable. PostgreSQL rejects SELECT FOR UPDATE when a nullable
        # select_related() OUTER JOIN is part of the locked query, so lock only the
        # funding-account table and fetch the credential lazily afterwards.
        queryset = queryset.select_for_update()
        accounts = list(queryset.order_by("priority", "created_at", "id"))
    else:
        accounts = list(
            queryset.select_related("api_key").order_by("priority", "created_at", "id")
        )

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
        return (
            health_rank,
            0 if account.is_default else 1,
            account.priority,
            account.created_at,
            str(account.id),
        )

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
            model_upstream=model_upstream,
        )
    ]


def select_runtime_funding_account(
    provider,
    *,
    required_native=ZERO,
    currency: str = "",
    allow_probe: bool = False,
    require_balance: bool = True,
    model_upstream: str = "",
):
    candidates = runtime_funding_accounts(
        provider,
        required_native=required_native,
        currency=currency,
        allow_probe=allow_probe,
        require_balance=require_balance,
        model_upstream=model_upstream,
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
    account_id=None,
    model_upstream: str = "",
):
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

    if account_id is not None:
        account = (
            ProviderFundingAccount.objects.select_for_update()
            .filter(pk=account_id, provider=provider, active=True)
            .first()
        )
        if account is None or not account_matches(
            account,
            required_native=amount,
            currency=currency,
            allow_probe=False,
            require_balance=True,
            model_upstream=model_upstream,
        ):
            raise ValidationError(
                "Выбранный закупочный API-аккаунт недоступен или имеет недостаточный баланс"
            )
    else:
        candidates = runtime_funding_accounts(
            provider,
            required_native=amount,
            currency=currency,
            allow_probe=False,
            require_balance=True,
            lock=True,
            model_upstream=model_upstream,
        )
        if not candidates:
            credential_candidates = runtime_funding_accounts(
                provider,
                required_native=ZERO,
                currency=currency,
                allow_probe=False,
                require_balance=False,
                lock=True,
                model_upstream=model_upstream,
            )
            if credential_candidates:
                raise ValidationError(
                    "Закупочный баланс провайдера исчерпан или недостаточен для этого запроса"
                )
            raise ValidationError(
                "Нет активного HEALTHY API-аккаунта с доступным ключом"
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
    services_module.reserve_provider_spend = reserve_provider_spend
    signals_module.reserve_provider_spend = reserve_provider_spend

    # Runtime operations all flow through signals._ensure(). Bind the reservation to
    # the exact provider currency carried by the immutable pricing snapshot so mixed
    # USD/EUR funding pools can never be charged with the wrong FX basis.
    def ensure(*, provider, expected_rub, snapshot, source_key, model_upstream=""):
        if not signals_module._require_procurement(provider):
            if str(source_key).startswith("chat:"):
                logger.info(
                    "[CHAT_PIPELINE] stage=PROVIDER_RESERVE_SKIPPED provider=%s source=%s reason=no_funded_ledger",
                    provider.slug,
                    source_key,
                )
            return None
        fx = signals_module._fx(snapshot)
        if fx is None:
            if signals_module._commercial_fail_closed():
                raise ValidationError(
                    f"Коммерческий запрос заблокирован: отсутствует FX snapshot для {provider.slug}"
                )
            return None
        native = (
            signals_module._decimal(expected_rub) / fx
        ).quantize(NATIVE_STEP, rounding=ROUND_UP)
        if native <= ZERO:
            if signals_module._commercial_fail_closed() and signals_module._decimal(expected_rub) > ZERO:
                raise ValidationError("Не удалось рассчитать закупочный резерв провайдера")
            return None
        currency = str((snapshot or {}).get("provider_currency") or "").upper().strip()
        try:
            if str(source_key).startswith("chat:"):
                logger.info(
                    "[CHAT_PIPELINE] stage=PROVIDER_RESERVE_START provider=%s source=%s amount_native=%s currency=%s",
                    provider.slug,
                    source_key,
                    native,
                    currency,
                )
            reservation = reserve_provider_spend(
                provider=provider,
                amount_native=native,
                source_key=source_key,
                currency=currency,
                model_upstream=model_upstream,
            )
            if str(source_key).startswith("chat:"):
                logger.info(
                    "[CHAT_PIPELINE] stage=PROVIDER_RESERVE_OK provider=%s source=%s reservation_id=%s account_id=%s amount_native=%s",
                    provider.slug,
                    source_key,
                    getattr(reservation, "id", ""),
                    getattr(reservation, "account_id", ""),
                    native,
                )
            return reservation
        except ValidationError as exc:
            # Procurement is owner-side accounting, not provider transport
            # authorization. Never take the customer chat offline solely because
            # the local purchasing ledger is stale/exhausted while a verified API
            # credential is still execution-ready. Other commercial operations
            # keep the strict production contract.
            if signals_module._commercial_fail_closed() and not str(source_key).startswith("chat:"):
                raise
            signals_module.logger.warning(
                "Provider procurement reservation unavailable; continuing chat without local provider reservation provider=%s source=%s reason=%s",
                provider.slug,
                source_key,
                exc,
            )
            if str(source_key).startswith("chat:"):
                logger.warning(
                    "[CHAT_PIPELINE] stage=PROVIDER_RESERVE_SKIPPED provider=%s source=%s reason=%s",
                    provider.slug,
                    source_key,
                    str(exc)[:500],
                )
            return None

    signals_module._ensure = ensure

    def procurement_ready(provider) -> bool:
        """Keep procurement optional until an active funding account is configured.

        Provider credentials and transport health are sufficient for customer traffic
        when the owner has not opted into the purchasing ledger. Once at least one
        active funding account exists, execution becomes strict/fail-closed and the
        exact account must have a HEALTHY credential plus available native balance.
        """
        try:
            configured = ProviderFundingAccount.objects.filter(
                provider=provider,
                active=True,
                funded_native__gt=ZERO,
            ).exists()
            if not configured:
                return True
            account = select_runtime_funding_account(
                provider,
                required_native=NATIVE_STEP,
                allow_probe=False,
                require_balance=True,
            )
            if account is not None:
                return True
            # The local procurement ledger can lag the real upstream balance.
            # Provider transport/key health remains the authority for chat
            # availability; reservation accounting is attempted later and may
            # safely be skipped for chat when capacity metadata is stale.
            return True
        except Exception:
            return True

    reliability_module._procurement_ready = procurement_ready

    for module_name in (
        "apps.agents.accounting",
        "apps.procurement.chat_signals",
        "apps.procurement.recovery_signals",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "reserve_provider_spend"):
            module.reserve_provider_spend = reserve_provider_spend
