from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response

from apps.ai_registry.models import AIModel, Provider
from apps.b2b_api.models import APIUsage
from apps.billing.models import FxRateSnapshot, RequestCost
from apps.billing.pricing import active_price, quote, require_margin
from apps.chat.models import CompareVariant
from apps.image_studio.models import ImageGeneration
from apps.procurement.models import (
    ProviderFundingAccount,
    ProviderPurchase,
    ProviderSpend,
    RetailTokenPriceVersion,
)
from apps.procurement.services import (
    create_funding_account,
    funding_summary,
    record_purchase,
    set_default_account,
)

from .services import audit
from .views import AdminAPIView

ZERO = Decimal("0")
MILLION = Decimal("1000000")


def _decimal(value, label, *, minimum=None):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DjangoValidationError(f"{label}: укажите число") from exc
    if minimum is not None and result < minimum:
        raise DjangoValidationError(f"{label}: значение должно быть не меньше {minimum}")
    return result


def _range(request):
    today = timezone.localdate()
    date_to = parse_date(request.query_params.get("to", "")) or today
    date_from = parse_date(request.query_params.get("from", "")) or (date_to - timedelta(days=29))
    if date_from > date_to:
        raise DjangoValidationError("Дата начала периода позже даты окончания")
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(timezone.datetime.combine(date_from, timezone.datetime.min.time()), tz)
    end = timezone.make_aware(
        timezone.datetime.combine(date_to + timedelta(days=1), timezone.datetime.min.time()), tz
    )
    return date_from, date_to, start, end


def _sum(queryset, field):
    return queryset.aggregate(value=Sum(field))["value"] or ZERO


def _latest_fx(currency):
    currency = str(currency or "RUB").upper()
    if currency == "RUB":
        return Decimal("1"), None
    item = (
        FxRateSnapshot.objects.filter(base_currency=currency, quote_currency="RUB")
        .order_by("-effective_at", "-created_at")
        .first()
    )
    return (item.rate, item) if item else (None, None)


def _provider_native_prices(price):
    input_native = price.input_price_per_million if price.input_price_per_million is not None else price.input_rub_per_million
    output_native = price.output_price_per_million if price.output_price_per_million is not None else price.output_rub_per_million
    return input_native, output_native


def _margin(sale, cost):
    if sale is None or sale <= 0:
        return None
    return ((sale - cost) / sale * Decimal("100")).quantize(Decimal("0.001"))


def _price_matrix(stress_usd):
    rows = []
    for model in AIModel.objects.select_related("provider").order_by("provider__name", "display_name"):
        try:
            price = active_price(model.slug)
        except DjangoValidationError:
            rows.append({"model": model.slug, "model_name": model.display_name, "provider": model.provider.slug, "provider_name": model.provider.name, "configured": False})
            continue
        retail = (
            RetailTokenPriceVersion.objects.filter(model_slug=model.slug, active=True, effective_from__lte=timezone.now())
            .order_by("-effective_from", "-created_at")
            .first()
        )
        fx_rate, fx = _latest_fx(price.provider_currency)
        input_native, output_native = _provider_native_prices(price)
        input_cost = input_native * fx_rate if fx_rate is not None else None
        output_cost = output_native * fx_rate if fx_rate is not None else None
        try:
            input_sale = retail.input_rub_per_million if retail else quote(price, 1_000_000, 0, provider_slug=model.provider.slug, model_slug=model.slug).user_charge_rub
            output_sale = retail.output_rub_per_million if retail else quote(price, 0, 1_000_000, provider_slug=model.provider.slug, model_slug=model.slug).user_charge_rub
        except DjangoValidationError:
            input_sale = output_sale = None
        stress_input = stress_output = stress_input_margin = stress_output_margin = None
        if price.provider_currency.upper() == "USD":
            stress_input = input_native * stress_usd
            stress_output = output_native * stress_usd
            stress_input_margin = _margin(input_sale, stress_input) if input_sale is not None else None
            stress_output_margin = _margin(output_sale, stress_output) if output_sale is not None else None
        rows.append({
            "model": model.slug,
            "model_name": model.display_name,
            "provider": model.provider.slug,
            "provider_name": model.provider.name,
            "configured": True,
            "currency": price.provider_currency,
            "provider_input_per_million_native": str(input_native),
            "provider_output_per_million_native": str(output_native),
            "fx_rate_rub": str(fx_rate) if fx_rate is not None else None,
            "fx_effective_at": fx.effective_at if fx else None,
            "provider_input_per_million_rub": str(input_cost) if input_cost is not None else None,
            "provider_output_per_million_rub": str(output_cost) if output_cost is not None else None,
            "retail_mode": "explicit" if retail else "markup",
            "retail_input_per_million_rub": str(input_sale) if input_sale is not None else None,
            "retail_output_per_million_rub": str(output_sale) if output_sale is not None else None,
            "input_margin_percent": str(_margin(input_sale, input_cost)) if input_sale is not None and input_cost is not None else None,
            "output_margin_percent": str(_margin(output_sale, output_cost)) if output_sale is not None and output_cost is not None else None,
            "stress_usd_rub": str(stress_usd) if price.provider_currency.upper() == "USD" else None,
            "stress_input_cost_rub": str(stress_input) if stress_input is not None else None,
            "stress_output_cost_rub": str(stress_output) if stress_output is not None else None,
            "stress_input_margin_percent": str(stress_input_margin) if stress_input_margin is not None else None,
            "stress_output_margin_percent": str(stress_output_margin) if stress_output_margin is not None else None,
            "stress_unprofitable": bool(
                (stress_input_margin is not None and stress_input_margin <= 0)
                or (stress_output_margin is not None and stress_output_margin <= 0)
            ),
        })
    return rows


class ProcurementEconomicsView(AdminAPIView):
    def get(self, request):
        try:
            date_from, date_to, start, end = _range(request)
            stress_usd = _decimal(request.query_params.get("stress_usd_rub", "200"), "Стресс-курс USD/RUB", minimum=Decimal("1"))
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=400)

        chat = RequestCost.objects.filter(created_at__gte=start, created_at__lt=end, charged_rub__isnull=False)
        b2b = APIUsage.objects.filter(created_at__gte=start, created_at__lt=end, state=APIUsage.State.COMPLETED)
        images = ImageGeneration.objects.filter(created_at__gte=start, created_at__lt=end, state=ImageGeneration.State.COMPLETED)
        compare = CompareVariant.objects.filter(created_at__gte=start, created_at__lt=end, state=CompareVariant.State.COMPLETED)
        purchases = ProviderPurchase.objects.filter(purchased_at__gte=start, purchased_at__lt=end)
        recognized = ProviderSpend.objects.filter(created_at__gte=start, created_at__lt=end)

        revenue = _sum(chat, "charged_rub") + _sum(b2b, "charged_rub") + _sum(images, "actual_cost_rub") + _sum(compare, "actual_cost_rub")
        nominal_cost = _sum(chat, "provider_cost_rub") + _sum(b2b, "provider_cost_rub") + _sum(images, "provider_cost_rub") + _sum(compare, "provider_cost_rub")
        recognized_economic_cost = _sum(recognized, "economic_cost_rub")
        procurement_outlay = _sum(purchases, "total_cash_outlay_rub")
        procurement_fees = _sum(purchases, "fees_rub")
        input_tokens = _sum(chat, "input_tokens") + _sum(b2b, "prompt_tokens") + _sum(compare, "input_tokens")
        output_tokens = _sum(chat, "output_tokens") + _sum(b2b, "completion_tokens") + _sum(compare, "output_tokens")
        gross_profit_nominal = revenue - nominal_cost
        gross_margin_nominal = gross_profit_nominal / revenue * Decimal("100") if revenue else ZERO
        economic_cost_for_margin = recognized_economic_cost if recognized.exists() else nominal_cost
        gross_profit_economic = revenue - economic_cost_for_margin
        gross_margin_economic = gross_profit_economic / revenue * Decimal("100") if revenue else ZERO

        accounts = list(ProviderFundingAccount.objects.select_related("provider").order_by("provider__name", "priority", "label"))
        account_data = [funding_summary(item) for item in accounts]
        source_count = chat.count() + b2b.count() + images.count() + compare.count()
        spend_count = recognized.count()
        allocation_coverage = Decimal(spend_count) / Decimal(source_count) * Decimal("100") if source_count else Decimal("100")
        prices = _price_matrix(stress_usd)

        return Response({
            "period": {"from": date_from, "to": date_to},
            "summary": {
                "revenue_rub": str(revenue),
                "nominal_provider_cost_rub": str(nominal_cost),
                "recognized_procurement_cost_rub": str(recognized_economic_cost),
                "procurement_cash_outlay_rub": str(procurement_outlay),
                "procurement_fees_rub": str(procurement_fees),
                "gross_profit_nominal_rub": str(gross_profit_nominal),
                "gross_margin_nominal_percent": str(gross_margin_nominal.quantize(Decimal("0.001"))),
                "gross_profit_economic_rub": str(gross_profit_economic),
                "gross_margin_economic_percent": str(gross_margin_economic.quantize(Decimal("0.001"))),
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "nominal_cost_per_million_total_tokens_rub": str((nominal_cost / Decimal(input_tokens + output_tokens) * MILLION).quantize(Decimal("0.01"))) if input_tokens + output_tokens else None,
                "allocation_coverage_percent": str(allocation_coverage.quantize(Decimal("0.01"))),
            },
            "risk": {
                "funding_accounts": len(accounts),
                "credentials_configured": sum(1 for item in account_data if item["credential_configured"]),
                "low_balance_accounts": sum(1 for item in account_data if item["low_balance"]),
                "unallocated_completed_operations": max(0, source_count - spend_count),
                "stress_unprofitable_models": sum(1 for row in prices if row.get("stress_unprofitable")),
            },
            "accounts": account_data,
            "purchases": [
                {
                    "id": str(item.id),
                    "account": str(item.account_id),
                    "account_label": item.account.label,
                    "provider": item.account.provider.slug,
                    "currency": item.account.currency,
                    "credit_native": str(item.credit_native),
                    "base_cost_rub": str(item.base_cost_rub),
                    "fees_rub": str(item.fees_rub),
                    "total_cash_outlay_rub": str(item.total_cash_outlay_rub),
                    "effective_cost_rub_per_native": str(item.effective_cost_rub_per_native),
                    "market_fx_rate_rub": str(item.market_fx_rate_rub) if item.market_fx_rate_rub is not None else None,
                    "reference": item.reference,
                    "purchased_at": item.purchased_at,
                }
                for item in purchases.select_related("account__provider")[:200]
            ],
            "prices": prices,
        })

    @transaction.atomic
    def post(self, request):
        action = str(request.data.get("action") or "").strip()
        try:
            if action == "create_account":
                provider = Provider.objects.get(slug=str(request.data.get("provider") or "").strip())
                account = create_funding_account(
                    provider=provider,
                    label=request.data.get("label"),
                    credential_env=request.data.get("credential_env"),
                    currency=request.data.get("currency") or "USD",
                    low_balance_native=request.data.get("low_balance_native") or 0,
                    priority=request.data.get("priority") or 100,
                    is_default=request.data.get("is_default") is True,
                    notes=request.data.get("notes") or "",
                )
                audit(request, "procurement.account_created", "provider_funding_account", account.id, {"provider": provider.slug, "credential_env": account.credential_env})
                return Response(funding_summary(account), status=201)

            if action == "set_default":
                account = ProviderFundingAccount.objects.select_related("provider").get(pk=request.data.get("account"))
                account = set_default_account(account)
                audit(request, "procurement.account_default", "provider_funding_account", account.id, {"provider": account.provider.slug})
                return Response(funding_summary(account))

            if action == "set_active":
                account = ProviderFundingAccount.objects.select_for_update().select_related("provider").get(pk=request.data.get("account"))
                active = request.data.get("active") is True
                if not active and account.is_default:
                    raise DjangoValidationError("Сначала назначьте другой основной закупочный аккаунт")
                account.active = active
                account.save(update_fields=["active", "updated_at"])
                audit(request, "procurement.account_active", "provider_funding_account", account.id, {"active": active})
                return Response(funding_summary(account))

            if action == "purchase":
                account = ProviderFundingAccount.objects.select_related("provider").get(pk=request.data.get("account"))
                purchased_at = timezone.now()
                raw_date = str(request.data.get("purchased_at") or "").strip()
                if raw_date:
                    parsed_date = parse_date(raw_date)
                    if parsed_date is None:
                        raise DjangoValidationError("Дата закупки должна быть YYYY-MM-DD")
                    purchased_at = timezone.make_aware(timezone.datetime.combine(parsed_date, timezone.datetime.min.time()), timezone.get_current_timezone())
                purchase = record_purchase(
                    account=account,
                    credit_native=_decimal(request.data.get("credit_native"), "API-баланс", minimum=Decimal("0.000001")),
                    base_cost_rub=_decimal(request.data.get("base_cost_rub"), "Стоимость закупки", minimum=ZERO),
                    fees_rub=_decimal(request.data.get("fees_rub") or 0, "Комиссии", minimum=ZERO),
                    market_fx_rate_rub=_decimal(request.data.get("market_fx_rate_rub"), "Курс", minimum=Decimal("0.000001")) if request.data.get("market_fx_rate_rub") not in (None, "") else None,
                    purchased_at=purchased_at,
                    created_by=request.user,
                    reference=request.data.get("reference") or "",
                )
                audit(request, "procurement.purchase_recorded", "provider_purchase", purchase.id, {"provider": account.provider.slug, "credit_native": str(purchase.credit_native), "total_cash_outlay_rub": str(purchase.total_cash_outlay_rub)})
                return Response({"id": str(purchase.id)}, status=201)

            if action == "retail_price":
                model = AIModel.objects.select_related("provider").get(slug=str(request.data.get("model") or "").strip())
                input_sale = _decimal(request.data.get("input_rub_per_million"), "Продажная цена input", minimum=Decimal("0.0001"))
                output_sale = _decimal(request.data.get("output_rub_per_million"), "Продажная цена output", minimum=Decimal("0.0001"))
                RetailTokenPriceVersion.objects.filter(model_slug=model.slug, active=True).update(active=False)
                retail = RetailTokenPriceVersion.objects.create(
                    model_slug=model.slug,
                    input_rub_per_million=input_sale,
                    output_rub_per_million=output_sale,
                    effective_from=timezone.now(),
                    reason=str(request.data.get("reason") or "")[:300],
                    created_by=request.user,
                )
                price = active_price(model.slug)
                require_margin(quote(price, 1_000_000, 0, provider_slug=model.provider.slug, model_slug=model.slug))
                require_margin(quote(price, 0, 1_000_000, provider_slug=model.provider.slug, model_slug=model.slug))
                audit(request, "procurement.retail_price_created", "retail_token_price", retail.id, {"model": model.slug, "input_rub_per_million": str(input_sale), "output_rub_per_million": str(output_sale)})
                return Response({"id": str(retail.id), "model": model.slug}, status=201)

            return Response({"detail": "Неизвестное действие"}, status=400)
        except (Provider.DoesNotExist, AIModel.DoesNotExist, ProviderFundingAccount.DoesNotExist):
            return Response({"detail": "Объект не найден"}, status=404)
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=400)
