from decimal import Decimal, InvalidOperation, ROUND_UP

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.response import Response

from apps.ai_registry.models import ProviderApiKey
from apps.procurement.models import ProviderFundingAccount, ProviderPurchase, ProviderSpendAllocation
from apps.procurement.services import create_funding_account, funding_summary, record_purchase, set_default_account

from .services import audit
from .views import AdminAPIView

ZERO = Decimal("0")
MONEY = Decimal("0.01")
RUB_STEP = Decimal("0.0001")
UNIT_STEP = Decimal("0.00000001")


def _decimal(value, label, *, required=False, minimum=None):
    if value in (None, ""):
        if required:
            raise DjangoValidationError(f"{label}: обязательное поле")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DjangoValidationError(f"{label}: укажите число") from exc
    if minimum is not None and result < minimum:
        raise DjangoValidationError(f"{label}: значение должно быть не меньше {minimum}")
    return result


def _purchase_time(raw):
    raw = str(raw or "").strip()
    if not raw:
        return timezone.now()
    value = parse_datetime(raw)
    if value is not None:
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    value_date = parse_date(raw)
    if value_date is None:
        raise DjangoValidationError("Дата закупки должна быть в ISO-формате")
    return timezone.make_aware(
        timezone.datetime.combine(value_date, timezone.datetime.min.time()),
        timezone.get_current_timezone(),
    )


def _purchase_payload(purchase):
    allocations = list(
        ProviderSpendAllocation.objects.filter(purchase=purchase)
        .select_related("spend")
        .order_by("created_at")
    )
    consumed = sum((item.native_amount for item in allocations), ZERO)
    economic_cost = sum((item.economic_cost_rub for item in allocations), ZERO)
    revenue = ZERO
    operations = set()
    for item in allocations:
        spend = item.spend
        operations.add(spend.id)
        if spend.native_cost > ZERO:
            revenue += spend.customer_charge_rub * item.native_amount / spend.native_cost
    revenue = revenue.quantize(MONEY)
    economic_cost = economic_cost.quantize(MONEY)
    profit = (revenue - economic_cost).quantize(MONEY)
    margin = (profit / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue > ZERO else ZERO
    remaining = max(ZERO, purchase.credit_native - consumed) if purchase.state == ProviderPurchase.State.ACTIVE else ZERO
    account = purchase.account
    key = account.api_key if account.api_key_id else None
    return {
        "id": str(purchase.id),
        "document_number": purchase.document_number,
        "state": purchase.state,
        "provider": account.provider.slug,
        "provider_name": account.provider.name,
        "account_id": str(account.id),
        "account_label": account.label,
        "api_key_id": str(key.id) if key else None,
        "api_key_label": key.label if key else "legacy env",
        "api_key_masked": key.masked if key else account.credential_env,
        "credit_native": str(purchase.credit_native),
        "credit_currency": account.currency,
        "consumed_native": str(consumed),
        "remaining_native": str(remaining),
        "payment_amount": str(purchase.payment_amount) if purchase.payment_amount is not None else None,
        "payment_currency": purchase.payment_currency,
        "payment_fx_rate_rub": str(purchase.payment_fx_rate_rub) if purchase.payment_fx_rate_rub is not None else None,
        "market_fx_rate_rub": str(purchase.market_fx_rate_rub) if purchase.market_fx_rate_rub is not None else None,
        "base_cost_rub": str(purchase.base_cost_rub),
        "fees_rub": str(purchase.fees_rub),
        "total_cash_outlay_rub": str(purchase.total_cash_outlay_rub),
        "effective_cost_rub_per_native": str(purchase.effective_cost_rub_per_native),
        "realized_revenue_rub": str(revenue),
        "realized_cost_rub": str(economic_cost),
        "realized_profit_rub": str(profit),
        "realized_margin_percent": str(margin),
        "operations_count": len(operations),
        "reference": purchase.reference,
        "purchased_at": purchase.purchased_at,
        "cancelled_at": purchase.cancelled_at,
        "deleted_at": purchase.deleted_at,
        "updated_at": purchase.updated_at,
        "created_at": purchase.created_at,
        "editable": purchase.state == ProviderPurchase.State.ACTIVE and not allocations,
        "cancellable": purchase.state == ProviderPurchase.State.ACTIVE and not allocations,
        "deletable": not allocations,
    }


def _lock_purchase(purchase_id):
    purchase = ProviderPurchase.objects.select_for_update().filter(pk=purchase_id).first()
    if purchase is None:
        raise DjangoValidationError("Закупочный ордер не найден")
    account = ProviderFundingAccount.objects.select_for_update().get(pk=purchase.account_id)
    if ProviderSpendAllocation.objects.filter(purchase=purchase).exists():
        raise DjangoValidationError(
            "Этот ордер уже участвовал в фактических списаниях. Изменение, отмена и удаление запрещены, чтобы не исказить FIFO и прибыль."
        )
    return purchase, account


def _unfund_order(purchase, account):
    new_funded = account.funded_native - purchase.credit_native
    required = account.spent_native + account.reserved_native
    if new_funded < required:
        raise DjangoValidationError(
            "Ордер нельзя отменить: его баланс уже нужен для фактических расходов или активных резервов."
        )
    account.funded_native = new_funded
    account.save(update_fields=["funded_native", "updated_at"])


def _edit_purchase(request):
    purchase, account = _lock_purchase(request.data.get("purchase_id"))
    if purchase.state != ProviderPurchase.State.ACTIVE:
        raise DjangoValidationError("Изменять можно только активный закупочный ордер")

    credit = _decimal(request.data.get("credit_native"), "Номинал API-баланса", required=True, minimum=Decimal("0.000001"))
    payment_amount = _decimal(request.data.get("payment_amount"), "Фактически оплачено", minimum=Decimal("0.000001"))
    payment_currency = str(request.data.get("payment_currency") or purchase.payment_currency).upper().strip()
    payment_fx = _decimal(request.data.get("payment_fx_rate_rub"), "Фактический курс", minimum=Decimal("0.000001"))
    base_cost = _decimal(request.data.get("base_cost_rub"), "Базовая стоимость в RUB", minimum=ZERO)
    fees = _decimal(request.data.get("fees_rub") or 0, "Комиссии", minimum=ZERO) or ZERO
    market_fx = _decimal(request.data.get("market_fx_rate_rub"), "Рыночный курс", minimum=Decimal("0.000001"))
    if len(payment_currency) != 3:
        raise DjangoValidationError("Валюта оплаты должна быть ISO 4217")
    if base_cost is None:
        if payment_amount is None:
            raise DjangoValidationError("Укажите фактическую оплату или базовую стоимость в RUB")
        if payment_currency == "RUB":
            base_cost = payment_amount
        elif payment_fx is not None:
            base_cost = payment_amount * payment_fx
        else:
            raise DjangoValidationError("Для оплаты не в RUB укажите фактический курс конвертации")
    total = (base_cost + fees).quantize(RUB_STEP)
    if total <= ZERO:
        raise DjangoValidationError("Фактическая стоимость закупки должна быть больше нуля")

    new_funded = account.funded_native - purchase.credit_native + credit
    if new_funded < account.spent_native + account.reserved_native:
        raise DjangoValidationError("Новый номинал ордера меньше уже использованного или зарезервированного баланса")
    account.funded_native = new_funded
    account.save(update_fields=["funded_native", "updated_at"])

    fields = {
        "credit_native": credit,
        "payment_amount": payment_amount,
        "payment_currency": payment_currency,
        "payment_fx_rate_rub": payment_fx,
        "base_cost_rub": base_cost,
        "fees_rub": fees,
        "total_cash_outlay_rub": total,
        "market_fx_rate_rub": market_fx,
        "effective_cost_rub_per_native": (total / credit).quantize(UNIT_STEP, rounding=ROUND_UP),
        "reference": str(request.data.get("reference") or "")[:300],
        "purchased_at": _purchase_time(request.data.get("purchased_at")),
        "updated_at": timezone.now(),
    }
    ProviderPurchase.objects.filter(pk=purchase.pk).update(**fields)
    purchase.refresh_from_db()
    audit(request, "procurement.purchase_edited", "provider_purchase", str(purchase.id), {
        "document_number": purchase.document_number,
        "credit_native": str(purchase.credit_native),
        "total_cash_outlay_rub": str(purchase.total_cash_outlay_rub),
    })
    return purchase


def _change_purchase_state(request, target_state):
    purchase, account = _lock_purchase(request.data.get("purchase_id"))
    if purchase.state == ProviderPurchase.State.DELETED:
        raise DjangoValidationError("Закупочный ордер уже удалён")
    now = timezone.now()
    if purchase.state == ProviderPurchase.State.ACTIVE:
        _unfund_order(purchase, account)
    updates = {"state": target_state, "updated_at": now}
    if target_state == ProviderPurchase.State.CANCELLED:
        updates["cancelled_at"] = now
    elif target_state == ProviderPurchase.State.DELETED:
        updates["deleted_at"] = now
    ProviderPurchase.objects.filter(pk=purchase.pk).update(**updates)
    purchase.refresh_from_db()
    audit(request, f"procurement.purchase_{target_state}", "provider_purchase", str(purchase.id), {
        "document_number": purchase.document_number,
        "state": purchase.state,
    })
    return purchase


class ProcurementLedgerView(AdminAPIView):
    def get(self, request):
        accounts = list(
            ProviderFundingAccount.objects.select_related("provider", "api_key")
            .order_by("provider__name", "priority", "label")
        )
        account_by_key = {str(item.api_key_id): item for item in accounts if item.api_key_id}
        keys = []
        for key in ProviderApiKey.objects.select_related("provider").order_by("provider__name", "priority", "created_at"):
            account = account_by_key.get(str(key.id))
            keys.append({
                "id": str(key.id),
                "provider": key.provider.slug,
                "provider_name": key.provider.name,
                "label": key.label,
                "masked": key.masked,
                "enabled": key.enabled,
                "health_state": key.health_state,
                "provider_balance_amount": str(key.balance_amount) if key.balance_amount is not None else None,
                "provider_balance_currency": key.balance_currency,
                "provider_balance_supported": key.balance_supported,
                "account_id": str(account.id) if account else None,
                "account_currency": account.currency if account else None,
                "ledger_available_native": str(account.available_native) if account else None,
                "ledger_spent_native": str(account.spent_native) if account else None,
                "ledger_reserved_native": str(account.reserved_native) if account else None,
                "is_default": bool(account.is_default) if account else False,
            })

        visible_purchases = list(
            ProviderPurchase.objects.exclude(state=ProviderPurchase.State.DELETED)
            .select_related("account__provider", "account__api_key")
            .order_by("-purchased_at", "-created_at")[:500]
        )
        purchase_rows = [_purchase_payload(item) for item in visible_purchases]
        active_purchases = [item for item in visible_purchases if item.state == ProviderPurchase.State.ACTIVE]
        active_rows = [row for row in purchase_rows if row["state"] == ProviderPurchase.State.ACTIVE]
        cash = sum((item.total_cash_outlay_rub for item in active_purchases), ZERO)
        fees = sum((item.fees_rub for item in active_purchases), ZERO)
        credit = sum((item.credit_native for item in active_purchases), ZERO)
        consumed = sum((Decimal(item["consumed_native"]) for item in active_rows), ZERO)
        revenue = sum((Decimal(item["realized_revenue_rub"]) for item in active_rows), ZERO)
        cost = sum((Decimal(item["realized_cost_rub"]) for item in active_rows), ZERO)
        profit = revenue - cost
        return Response({
            "summary": {
                "purchase_documents": len(active_purchases),
                "cash_outlay_rub": str(cash.quantize(MONEY)),
                "fees_rub": str(fees.quantize(MONEY)),
                "credit_native_total": str(credit),
                "consumed_native_total": str(consumed),
                "realized_revenue_rub": str(revenue.quantize(MONEY)),
                "realized_cost_rub": str(cost.quantize(MONEY)),
                "realized_profit_rub": str(profit.quantize(MONEY)),
                "realized_margin_percent": str((profit / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue > ZERO else ZERO),
            },
            "keys": keys,
            "accounts": [funding_summary(item) for item in accounts],
            "purchases": purchase_rows,
        })

    @transaction.atomic
    def post(self, request):
        action = str(request.data.get("action") or "purchase_key").strip()
        try:
            if action == "set_default":
                account = ProviderFundingAccount.objects.select_related("provider", "api_key").get(pk=request.data.get("account_id"))
                result = set_default_account(account)
                audit(request, "procurement.account_defaulted", "provider_funding_account", str(result.id), {"provider": result.provider.slug})
                return Response(funding_summary(result))
            if action == "edit_purchase":
                return Response(_purchase_payload(_edit_purchase(request)))
            if action == "cancel_purchase":
                return Response(_purchase_payload(_change_purchase_state(request, ProviderPurchase.State.CANCELLED)))
            if action == "delete_purchase":
                return Response(_purchase_payload(_change_purchase_state(request, ProviderPurchase.State.DELETED)))
            if action != "purchase_key":
                return Response({"detail": "Неизвестное действие"}, status=400)

            key = ProviderApiKey.objects.select_related("provider").get(pk=request.data.get("api_key_id"))
            credit_currency = str(request.data.get("credit_currency") or "USD").upper().strip()
            if len(credit_currency) != 3:
                raise DjangoValidationError("Валюта API-баланса должна быть ISO 4217")
            try:
                account = key.funding_account
            except ProviderFundingAccount.DoesNotExist:
                account = create_funding_account(
                    provider=key.provider,
                    api_key=key,
                    label=key.label,
                    currency=credit_currency,
                    low_balance_native=request.data.get("low_balance_native") or 0,
                    priority=key.priority,
                    is_default=not ProviderFundingAccount.objects.filter(provider=key.provider, is_default=True).exists(),
                    notes="Автоматически создан при первой закупке API-ключа",
                )
            if account.currency != credit_currency:
                raise DjangoValidationError(
                    f"Этот ключ уже учитывается в {account.currency}; для другой валюты создайте отдельный API-ключ/закупочный счёт"
                )

            purchase = record_purchase(
                account=account,
                credit_native=_decimal(request.data.get("credit_native"), "Номинал API-баланса", required=True, minimum=Decimal("0.000001")),
                payment_amount=_decimal(request.data.get("payment_amount"), "Фактически оплачено", minimum=Decimal("0.000001")),
                payment_currency=request.data.get("payment_currency") or "RUB",
                payment_fx_rate_rub=_decimal(request.data.get("payment_fx_rate_rub"), "Фактический курс конвертации", minimum=Decimal("0.000001")),
                base_cost_rub=_decimal(request.data.get("base_cost_rub"), "Базовая стоимость в RUB", minimum=ZERO),
                fees_rub=_decimal(request.data.get("fees_rub") or 0, "Комиссии в RUB", minimum=ZERO),
                market_fx_rate_rub=_decimal(request.data.get("market_fx_rate_rub"), "Рыночный курс", minimum=Decimal("0.000001")),
                purchased_at=_purchase_time(request.data.get("purchased_at")),
                created_by=request.user,
                reference=request.data.get("reference") or "",
            )
            audit(
                request,
                "procurement.purchase_recorded",
                "provider_purchase",
                str(purchase.id),
                {
                    "document_number": purchase.document_number,
                    "provider": key.provider.slug,
                    "api_key_id": str(key.id),
                    "credit_native": str(purchase.credit_native),
                    "total_cash_outlay_rub": str(purchase.total_cash_outlay_rub),
                },
            )
            return Response(_purchase_payload(purchase), status=201)
        except (ProviderApiKey.DoesNotExist, ProviderFundingAccount.DoesNotExist):
            return Response({"detail": "API-ключ или закупочный аккаунт не найден"}, status=404)
        except DjangoValidationError as exc:
            return Response({"detail": getattr(exc, "messages", None) or [str(exc)]}, status=400)
