import os
from decimal import Decimal, ROUND_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.ai_registry.models import Provider
from apps.billing.models import CostAnomaly

from .models import (
    ProviderFundingAccount,
    ProviderPurchase,
    ProviderSpend,
    ProviderSpendReservation,
)

ZERO = Decimal("0")
NATIVE_STEP = Decimal("0.000001")
RUB_STEP = Decimal("0.0001")


def _d(value):
    return Decimal(str(value))


def account_available_native(account):
    return (account.funded_native - account.reserved_native - account.spent_native).quantize(NATIVE_STEP)


def account_weighted_unit_cost_rub(account):
    aggregate = account.purchases.aggregate(
        credit=Sum("credit_native"),
        cash=Sum("total_cash_outlay_rub"),
    )
    credit = aggregate["credit"] or ZERO
    cash = aggregate["cash"] or ZERO
    if credit <= ZERO:
        return ZERO
    return (cash / credit).quantize(Decimal("0.00000001"), rounding=ROUND_UP)


@transaction.atomic
def create_funding_account(*, provider, label, credential_env, currency, low_balance_native=0, priority=100, is_default=False, notes=""):
    credential_env = str(credential_env or "").strip()
    if not credential_env:
        raise ValidationError("Укажите имя env-переменной, в которой хранится API-ключ")
    if not credential_env.replace("_", "A").isalnum() or credential_env.upper() != credential_env:
        raise ValidationError("Имя env-переменной должно быть в формате PROVIDER_API_KEY_1")
    if ProviderFundingAccount.objects.filter(provider=provider, credential_env=credential_env).exists():
        raise ValidationError("Такой закупочный аккаунт уже существует")
    if is_default:
        ProviderFundingAccount.objects.filter(provider=provider, is_default=True).update(is_default=False)
    account = ProviderFundingAccount.objects.create(
        provider=provider,
        label=str(label or credential_env).strip()[:160],
        credential_env=credential_env,
        currency=str(currency or "USD").upper()[:3],
        low_balance_native=max(ZERO, _d(low_balance_native or 0)),
        priority=max(0, int(priority or 0)),
        is_default=bool(is_default),
        notes=str(notes or ""),
    )
    if account.is_default:
        Provider.objects.filter(pk=provider.pk).update(credential_env=account.credential_env)
    return account


@transaction.atomic
def set_default_account(account):
    account = ProviderFundingAccount.objects.select_for_update().select_related("provider").get(pk=account.pk)
    ProviderFundingAccount.objects.filter(provider=account.provider, is_default=True).exclude(pk=account.pk).update(is_default=False)
    account.is_default = True
    account.active = True
    account.save(update_fields=["is_default", "active", "updated_at"])
    Provider.objects.filter(pk=account.provider_id).update(credential_env=account.credential_env)
    return account


@transaction.atomic
def record_purchase(*, account, credit_native, base_cost_rub, fees_rub, purchased_at, created_by, market_fx_rate_rub=None, reference=""):
    account = ProviderFundingAccount.objects.select_for_update().get(pk=account.pk)
    credit = _d(credit_native)
    base = _d(base_cost_rub)
    fees = _d(fees_rub or 0)
    if credit <= 0:
        raise ValidationError("Закупленный API-баланс должен быть больше нуля")
    if base < 0 or fees < 0 or base + fees <= 0:
        raise ValidationError("Фактическая стоимость закупки должна быть больше нуля")
    total = (base + fees).quantize(RUB_STEP)
    unit = (total / credit).quantize(Decimal("0.00000001"), rounding=ROUND_UP)
    purchase = ProviderPurchase.objects.create(
        account=account,
        credit_native=credit,
        base_cost_rub=base,
        fees_rub=fees,
        total_cash_outlay_rub=total,
        market_fx_rate_rub=_d(market_fx_rate_rub) if market_fx_rate_rub not in (None, "") else None,
        effective_cost_rub_per_native=unit,
        reference=str(reference or "")[:300],
        purchased_at=purchased_at or timezone.now(),
        created_by=created_by,
    )
    account.funded_native += credit
    account.save(update_fields=["funded_native", "updated_at"])
    return purchase


def default_account(provider):
    return ProviderFundingAccount.objects.filter(
        provider=provider,
        active=True,
        is_default=True,
    ).first()


def credential_is_configured(account):
    return bool(os.getenv(account.credential_env, "").strip())


@transaction.atomic
def reserve_provider_spend(*, provider, amount_native, source_key):
    amount = _d(amount_native).quantize(NATIVE_STEP, rounding=ROUND_UP)
    if amount <= ZERO:
        return None
    existing = ProviderSpendReservation.objects.select_related("account").filter(source_key=source_key).first()
    if existing:
        return existing
    accounts = list(
        ProviderFundingAccount.objects.select_for_update()
        .filter(provider=provider, active=True)
        .order_by("-is_default", "priority", "created_at")
    )
    if not accounts:
        return None
    for account in accounts:
        if not credential_is_configured(account):
            continue
        if account_available_native(account) < amount:
            continue
        account.reserved_native += amount
        account.save(update_fields=["reserved_native", "updated_at"])
        return ProviderSpendReservation.objects.create(
            account=account,
            amount_native=amount,
            source_key=source_key,
        )
    raise ValidationError("Закупленный баланс AI-провайдера исчерпан или API-ключ не настроен")


@transaction.atomic
def release_provider_spend(reservation_id):
    if not reservation_id:
        return None
    reservation = ProviderSpendReservation.objects.select_for_update().select_related("account").get(pk=reservation_id)
    if reservation.state != ProviderSpendReservation.State.ACTIVE:
        return reservation
    account = ProviderFundingAccount.objects.select_for_update().get(pk=reservation.account_id)
    account.reserved_native -= reservation.amount_native
    if account.reserved_native < ZERO:
        raise ValidationError("Нарушен резерв закупочного баланса провайдера")
    account.save(update_fields=["reserved_native", "updated_at"])
    reservation.state = ProviderSpendReservation.State.RELEASED
    reservation.settled_at = timezone.now()
    reservation.save(update_fields=["state", "settled_at"])
    return reservation


@transaction.atomic
def settle_provider_spend(*, reservation_id, actual_native, nominal_cost_rub, source_type, source_id, model_slug="", provider_request_id="", input_tokens=0, output_tokens=0):
    if not reservation_id:
        return None
    reservation = ProviderSpendReservation.objects.select_for_update().select_related("account__provider").get(pk=reservation_id)
    existing = ProviderSpend.objects.filter(reservation=reservation).first()
    if existing:
        return existing
    if reservation.state != ProviderSpendReservation.State.ACTIVE:
        raise ValidationError("Закупочный резерв уже закрыт")
    actual = _d(actual_native).quantize(NATIVE_STEP, rounding=ROUND_UP)
    if actual < ZERO:
        raise ValidationError("Фактический расход провайдера не может быть отрицательным")
    if actual > reservation.amount_native:
        provider = reservation.account.provider
        Provider.objects.filter(pk=provider.pk).update(
            emergency_disabled=True,
            health_state=Provider.HealthState.DISABLED,
        )
        CostAnomaly.objects.get_or_create(
            dedupe_key=f"provider-procurement-overrun:{source_type}:{source_id}",
            defaults={
                "kind": CostAnomaly.Kind.COST_DEVIATION,
                "severity": "critical",
                "provider_slug": provider.slug,
                "model_slug": model_slug,
                "expected_rub": _d(nominal_cost_rub),
                "actual_rub": _d(nominal_cost_rub),
                "details": {
                    "reason": "provider_native_cost_exceeded_reserved_capacity",
                    "reserved_native": str(reservation.amount_native),
                    "actual_native": str(actual),
                },
            },
        )
        raise ValidationError("Фактический расход провайдера превысил закупочный резерв; провайдер аварийно отключён")
    account = ProviderFundingAccount.objects.select_for_update().get(pk=reservation.account_id)
    account.reserved_native -= reservation.amount_native
    account.spent_native += actual
    if account.spent_native + account.reserved_native > account.funded_native:
        raise ValidationError("Закупочный баланс провайдера исчерпан")
    account.save(update_fields=["reserved_native", "spent_native", "updated_at"])
    unit = account_weighted_unit_cost_rub(account)
    economic = (actual * unit).quantize(RUB_STEP, rounding=ROUND_UP)
    spend = ProviderSpend.objects.create(
        account=account,
        reservation=reservation,
        source_type=str(source_type)[:40],
        source_id=str(source_id)[:160],
        model_slug=str(model_slug)[:160],
        provider_request_id=str(provider_request_id or "")[:200],
        input_tokens=max(0, int(input_tokens or 0)),
        output_tokens=max(0, int(output_tokens or 0)),
        native_cost=actual,
        nominal_cost_rub=_d(nominal_cost_rub).quantize(RUB_STEP),
        economic_cost_rub=economic,
        acquisition_unit_cost_rub=unit,
    )
    reservation.actual_native = actual
    reservation.state = ProviderSpendReservation.State.SETTLED
    reservation.settled_at = timezone.now()
    reservation.save(update_fields=["actual_native", "state", "settled_at"])
    return spend


def funding_summary(account):
    unit = account_weighted_unit_cost_rub(account)
    available = account_available_native(account)
    estimated_available_value_rub = (available * unit).quantize(RUB_STEP)
    return {
        "id": str(account.id),
        "provider": account.provider.slug,
        "provider_name": account.provider.name,
        "label": account.label,
        "credential_env": account.credential_env,
        "credential_configured": credential_is_configured(account),
        "currency": account.currency,
        "active": account.active,
        "is_default": account.is_default,
        "priority": account.priority,
        "funded_native": str(account.funded_native),
        "reserved_native": str(account.reserved_native),
        "spent_native": str(account.spent_native),
        "available_native": str(available),
        "low_balance_native": str(account.low_balance_native),
        "weighted_acquisition_rub_per_native": str(unit),
        "estimated_available_value_rub": str(estimated_available_value_rub),
        "low_balance": available <= account.low_balance_native,
    }
