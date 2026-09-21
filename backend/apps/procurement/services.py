import os
import uuid
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
    ProviderSpendAllocation,
    ProviderSpendReservation,
)

ZERO = Decimal("0")
NATIVE_STEP = Decimal("0.000001")
RUB_STEP = Decimal("0.0001")


def _d(value):
    return Decimal(str(value))


def account_available_native(account):
    return (account.funded_native - account.reserved_native - account.spent_native).quantize(NATIVE_STEP)


def _purchase_remaining_rows(account):
    purchases = list(
        ProviderPurchase.objects.select_for_update()
        .filter(account=account)
        .order_by("purchased_at", "created_at", "id")
    )
    allocated = {
        row["purchase_id"]: row["value"] or ZERO
        for row in ProviderSpendAllocation.objects.filter(purchase__account=account)
        .values("purchase_id")
        .annotate(value=Sum("native_amount"))
    }
    allocated_total = sum(allocated.values(), ZERO)
    legacy_unallocated_spend = max(ZERO, account.spent_native - allocated_total)
    rows = []
    for purchase in purchases:
        remaining = max(ZERO, purchase.credit_native - allocated.get(purchase.id, ZERO))
        if legacy_unallocated_spend > ZERO and remaining > ZERO:
            legacy_take = min(remaining, legacy_unallocated_spend)
            remaining -= legacy_take
            legacy_unallocated_spend -= legacy_take
        rows.append((purchase, remaining))
    return rows


def account_weighted_unit_cost_rub(account):
    # Value only the still-unconsumed procurement inventory. This keeps the
    # displayed average meaningful after differently-priced top-ups are spent.
    try:
        rows = _purchase_remaining_rows(account)
    except Exception:
        aggregate = account.purchases.aggregate(credit=Sum("credit_native"), cash=Sum("total_cash_outlay_rub"))
        credit = aggregate["credit"] or ZERO
        cash = aggregate["cash"] or ZERO
        if credit <= ZERO:
            return ZERO
        return (cash / credit).quantize(Decimal("0.00000001"), rounding=ROUND_UP)
    credit = ZERO
    value = ZERO
    for purchase, remaining in rows:
        if remaining <= ZERO:
            continue
        credit += remaining
        value += remaining * purchase.effective_cost_rub_per_native
    if credit <= ZERO:
        return ZERO
    return (value / credit).quantize(Decimal("0.00000001"), rounding=ROUND_UP)


@transaction.atomic
def create_funding_account(*, provider, label, credential_env="", api_key=None, currency="USD", low_balance_native=0, priority=100, is_default=False, notes=""):
    credential_env = str(credential_env or "").strip()
    if api_key is None and not credential_env:
        raise ValidationError("Выберите API-ключ для закупочного аккаунта")
    if api_key is not None:
        if api_key.provider_id != provider.id:
            raise ValidationError("API-ключ принадлежит другому провайдеру")
        if ProviderFundingAccount.objects.filter(api_key=api_key).exists():
            raise ValidationError("Для этого API-ключа закупочный аккаунт уже создан")
    if credential_env:
        if not credential_env.replace("_", "A").isalnum() or credential_env.upper() != credential_env:
            raise ValidationError("Имя env-переменной должно быть в формате PROVIDER_API_KEY_1")
        if ProviderFundingAccount.objects.filter(provider=provider, credential_env=credential_env).exists():
            raise ValidationError("Такой закупочный аккаунт уже существует")
    if is_default:
        ProviderFundingAccount.objects.filter(provider=provider, is_default=True).update(is_default=False)
    account = ProviderFundingAccount.objects.create(
        provider=provider,
        api_key=api_key,
        label=str(label or (api_key.label if api_key is not None else credential_env)).strip()[:160],
        credential_env=credential_env,
        currency=str(currency or "USD").upper()[:3],
        low_balance_native=max(ZERO, _d(low_balance_native or 0)),
        priority=max(0, int(priority or 0)),
        is_default=bool(is_default),
        notes=str(notes or ""),
    )
    if account.is_default and account.credential_env:
        Provider.objects.filter(pk=provider.pk).update(credential_env=account.credential_env)
    return account


@transaction.atomic
def set_default_account(account):
    # Lock only ProviderFundingAccount. Joining nullable api_key under FOR UPDATE
    # is rejected by PostgreSQL (nullable side of an outer join).
    account = ProviderFundingAccount.objects.select_for_update().select_related("provider").get(pk=account.pk)
    ProviderFundingAccount.objects.filter(provider=account.provider, is_default=True).exclude(pk=account.pk).update(is_default=False)
    account.is_default = True
    account.active = True
    account.save(update_fields=["is_default", "active", "updated_at"])
    if account.api_key_id:
        api_key = account.api_key
        api_key.enabled = True
        if api_key.health_state == "disabled":
            api_key.health_state = "unknown"
        api_key.save(update_fields=["enabled", "health_state"])
    elif account.credential_env:
        Provider.objects.filter(pk=account.provider_id).update(credential_env=account.credential_env)
    return account


@transaction.atomic
def record_purchase(
    *,
    account,
    credit_native,
    base_cost_rub=None,
    fees_rub=0,
    purchased_at=None,
    created_by,
    market_fx_rate_rub=None,
    payment_amount=None,
    payment_currency="RUB",
    payment_fx_rate_rub=None,
    reference="",
):
    account = ProviderFundingAccount.objects.select_for_update().get(pk=account.pk)
    credit = _d(credit_native)
    fees = _d(fees_rub or 0)
    payment_currency = str(payment_currency or "RUB").upper().strip()
    payment = _d(payment_amount) if payment_amount not in (None, "") else None
    payment_fx = _d(payment_fx_rate_rub) if payment_fx_rate_rub not in (None, "") else None
    if credit <= 0:
        raise ValidationError("Закупленный API-баланс должен быть больше нуля")
    if len(payment_currency) != 3:
        raise ValidationError("Валюта оплаты должна быть ISO 4217, например RUB или USD")
    if payment is not None and payment <= ZERO:
        raise ValidationError("Сумма фактической оплаты должна быть больше нуля")
    if payment_fx is not None and payment_fx <= ZERO:
        raise ValidationError("Курс оплаты должен быть больше нуля")

    if base_cost_rub in (None, ""):
        if payment is None:
            raise ValidationError("Укажите стоимость закупки в рублях или фактическую сумму оплаты")
        if payment_currency == "RUB":
            base = payment
        else:
            if payment_fx is None:
                raise ValidationError("Для оплаты не в рублях укажите фактический курс конвертации в RUB")
            base = payment * payment_fx
    else:
        base = _d(base_cost_rub)

    if base < ZERO or fees < ZERO or base + fees <= ZERO:
        raise ValidationError("Фактическая стоимость закупки должна быть больше нуля")
    purchased_at = purchased_at or timezone.now()
    total = (base + fees).quantize(RUB_STEP)
    unit = (total / credit).quantize(Decimal("0.00000001"), rounding=ROUND_UP)
    purchase_id = uuid.uuid4()
    document_number = f"API-{purchased_at:%Y%m%d}-{str(purchase_id).replace('-', '')[:8].upper()}"
    purchase = ProviderPurchase.objects.create(
        id=purchase_id,
        document_number=document_number,
        account=account,
        credit_native=credit,
        payment_amount=payment,
        payment_currency=payment_currency,
        payment_fx_rate_rub=payment_fx,
        base_cost_rub=base,
        fees_rub=fees,
        total_cash_outlay_rub=total,
        market_fx_rate_rub=_d(market_fx_rate_rub) if market_fx_rate_rub not in (None, "") else None,
        effective_cost_rub_per_native=unit,
        reference=str(reference or "")[:300],
        purchased_at=purchased_at,
        created_by=created_by,
    )
    account.funded_native += credit
    account.save(update_fields=["funded_native", "updated_at"])
    return purchase


def default_account(provider):
    return ProviderFundingAccount.objects.filter(provider=provider, active=True, is_default=True).first()


def credential_is_configured(account):
    if account.api_key_id:
        try:
            return bool(account.api_key.enabled and account.api_key.get_secret())
        except Exception:
            return False
    return bool(account.credential_env and os.getenv(account.credential_env, "").strip())


@transaction.atomic
def reserve_provider_spend(*, provider, amount_native, source_key):
    amount = _d(amount_native).quantize(NATIVE_STEP, rounding=ROUND_UP)
    if amount <= ZERO:
        return None
    existing = ProviderSpendReservation.objects.select_related("account").filter(source_key=source_key).first()
    if existing:
        return existing
    # Lock only the funding-account row. api_key is nullable, so select_related
    # here would create an OUTER JOIN that PostgreSQL refuses to lock.
    account = (
        ProviderFundingAccount.objects.select_for_update()
        .filter(provider=provider, active=True, is_default=True)
        .first()
    )
    if account is None:
        raise ValidationError("Для коммерческого провайдера не настроен основной закупочный аккаунт")
    if account.api_key_id:
        api_key = account.api_key
        if not api_key.enabled:
            raise ValidationError("API-ключ закупочного аккаунта отключён")
    elif account.credential_env != provider.credential_env:
        raise ValidationError("Основной закупочный аккаунт не совпадает с API-ключом активного провайдера")
    if not credential_is_configured(account):
        raise ValidationError("API-ключ закупочного аккаунта не настроен на сервере")
    if account_available_native(account) < amount:
        raise ValidationError("Закупленный баланс AI-провайдера исчерпан")
    account.reserved_native += amount
    account.save(update_fields=["reserved_native", "updated_at"])
    return ProviderSpendReservation.objects.create(account=account, amount_native=amount, source_key=source_key)


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
def settle_provider_spend(*, reservation_id, actual_native, nominal_cost_rub, customer_charge_rub=0, source_type, source_id, model_slug="", provider_request_id="", input_tokens=0, output_tokens=0):
    if not reservation_id:
        return None
    reservation = ProviderSpendReservation.objects.select_for_update().select_related("account__provider").get(pk=reservation_id)
    existing = ProviderSpend.objects.filter(reservation=reservation).first()
    if existing:
        return existing
    if reservation.state != ProviderSpendReservation.State.ACTIVE:
        return None
    actual = _d(actual_native).quantize(NATIVE_STEP, rounding=ROUND_UP)
    customer_charge = _d(customer_charge_rub).quantize(RUB_STEP)
    if actual < ZERO:
        raise ValidationError("Фактический расход провайдера не может быть отрицательным")
    if customer_charge < ZERO:
        raise ValidationError("Выручка операции не может быть отрицательной")

    account = ProviderFundingAccount.objects.select_for_update().get(pk=reservation.account_id)
    overrun = actual > reservation.amount_native
    provider = reservation.account.provider
    account.reserved_native -= reservation.amount_native
    available_after_release = max(ZERO, account.funded_native - account.spent_native)
    ledger_spend = min(actual, available_after_release)

    allocation_plan = []
    remaining_to_allocate = ledger_spend
    economic = ZERO
    if remaining_to_allocate > ZERO:
        for purchase, remaining in _purchase_remaining_rows(account):
            if remaining_to_allocate <= ZERO:
                break
            if remaining <= ZERO:
                continue
            take = min(remaining, remaining_to_allocate).quantize(NATIVE_STEP)
            cost = (take * purchase.effective_cost_rub_per_native).quantize(RUB_STEP, rounding=ROUND_UP)
            allocation_plan.append((purchase, take, cost))
            economic += cost
            remaining_to_allocate -= take
    if remaining_to_allocate > ZERO:
        raise ValidationError("Не удалось распределить расход по документам закупки API")

    unit = (economic / ledger_spend).quantize(Decimal("0.00000001"), rounding=ROUND_UP) if ledger_spend > ZERO else ZERO
    account.spent_native += ledger_spend
    account.save(update_fields=["reserved_native", "spent_native", "updated_at"])

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
        economic_cost_rub=economic.quantize(RUB_STEP),
        customer_charge_rub=customer_charge,
        acquisition_unit_cost_rub=unit,
    )
    for purchase, native_amount, cost in allocation_plan:
        ProviderSpendAllocation.objects.create(
            spend=spend,
            purchase=purchase,
            native_amount=native_amount,
            economic_cost_rub=cost,
        )

    reservation.actual_native = min(actual, reservation.amount_native)
    reservation.state = ProviderSpendReservation.State.SETTLED
    reservation.settled_at = timezone.now()
    reservation.save(update_fields=["actual_native", "state", "settled_at"])

    if overrun:
        unallocated = (actual - ledger_spend).quantize(NATIVE_STEP)
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
                "actual_rub": economic,
                "details": {
                    "reason": "provider_native_cost_exceeded_reserved_capacity",
                    "reserved_native": str(reservation.amount_native),
                    "actual_native": str(actual),
                    "ledger_spend_native": str(ledger_spend),
                    "unallocated_native": str(unallocated),
                    "customer_charge_rub": str(customer_charge),
                    "provider_disabled": True,
                },
            },
        )
    return spend


def funding_summary(account):
    unit = account_weighted_unit_cost_rub(account)
    available = account_available_native(account)
    estimated_available_value_rub = (available * unit).quantize(RUB_STEP)
    key = account.api_key if account.api_key_id else None
    return {
        "id": str(account.id),
        "provider": account.provider.slug,
        "provider_name": account.provider.name,
        "api_key_id": str(key.id) if key else None,
        "api_key_label": key.label if key else "",
        "api_key_masked": key.masked if key else "",
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
