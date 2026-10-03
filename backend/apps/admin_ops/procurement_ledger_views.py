from decimal import Decimal, InvalidOperation, ROUND_UP

import httpx
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.response import Response

from apps.ai_registry.models import AIModel, ProviderApiKey
from apps.billing.models import MarkupRuleVersion, PriceVersion
from apps.procurement.models import ProviderFundingAccount, ProviderPurchase, ProviderSpendAllocation
from apps.procurement.services import create_funding_account, funding_summary, provider_pricing_currency, record_purchase, set_default_account

from .services import audit
from .views import AdminAPIView

ZERO = Decimal("0")
MONEY = Decimal("0.01")
RUB_STEP = Decimal("0.0001")
UNIT_STEP = Decimal("0.00000001")
MILLION = Decimal("1000000")


def _first_decimal(mapping, *keys):
    for key in keys:
        value = mapping.get(key) if isinstance(mapping, dict) else None
        if value in (None, ""):
            continue
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _per_million_from_generic(value):
    if value is None:
        return None
    value = Decimal(value)
    # OpenAI/OpenRouter-compatible catalogs usually expose per-token prices in
    # generic prompt/completion fields. Explicit *_per_million fields bypass this.
    return (value * MILLION) if value < Decimal("1") else value


def _decimal_or_none(value):
    if value in (None, "", {}, []):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _polza_price_row(item):
    top_provider = (
        item.get("top_provider")
        if isinstance(item.get("top_provider"), dict)
        else {}
    )
    pricing = (
        top_provider.get("pricing")
        if isinstance(top_provider.get("pricing"), dict)
        else {}
    )
    currency = str(pricing.get("currency") or "RUB").upper().strip()[:3] or "RUB"

    input_pm = _decimal_or_none(pricing.get("prompt_per_million"))
    output_pm = _decimal_or_none(pricing.get("completion_per_million"))
    image_input_pm = _decimal_or_none(pricing.get("image_input_per_million"))
    image_output_pm = _decimal_or_none(pricing.get("image_output_per_million"))

    per_request = _decimal_or_none(pricing.get("per_request"))
    tiers = pricing.get("tiers") if isinstance(pricing.get("tiers"), list) else []
    normalized_tiers = []
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        cost = _decimal_or_none(tier.get("cost_rub"))
        if cost is None:
            continue
        normalized_tiers.append(
            {
                "conditions": [
                    str(value)
                    for value in (tier.get("conditions") or [])
                    if value not in (None, "")
                ],
                "cost_rub": str(cost),
            }
        )

    return {
        "currency": currency,
        "input_per_million": str(input_pm) if input_pm is not None else None,
        "output_per_million": str(output_pm) if output_pm is not None else None,
        "image_input_per_million": str(image_input_pm) if image_input_pm is not None else None,
        "image_output_per_million": str(image_output_pm) if image_output_pm is not None else None,
        "image_per_image": str(per_request) if per_request is not None else None,
        "pricing_tiers": normalized_tiers,
        "model_type": str(item.get("type") or ""),
    }


def _polza_pricing_for_key(key):
    """Load current Polza pricing for exactly this credential.

    Prefer the pricing catalog when available. If Polza changes or temporarily
    disables that endpoint, fall back to the credential-specific /models catalog
    so procurement can still be recorded with manual price overrides.
    """
    base_url = str(key.provider.api_base_url or "https://polza.ai/api/v1").rstrip("/")
    headers = {
        "Authorization": f"Bearer {key.get_secret()}",
        "Accept-Language": "ru",
    }
    allowed = set(str(value) for value in (key.allowed_models or []) if value)
    if not allowed:
        return []
    rows_by_id = {}

    catalog_error = None
    try:
        page = 1
        total_pages = 1
        while page <= total_pages and page <= 50:
            response = httpx.get(
                f"{base_url}/models/catalog",
                headers=headers,
                params={"page": page, "limit": 100},
                timeout=20,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data", []) if isinstance(payload, dict) else []
            for item in data:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id") or "").strip()
                if not model_id:
                    continue
                if allowed and model_id not in allowed:
                    continue
                rows_by_id[model_id] = {
                    "id": model_id,
                    "display_name": str(item.get("name") or item.get("display_name") or model_id),
                    **_polza_price_row(item),
                    "pricing_available": True,
                }
            meta = payload.get("meta") if isinstance(payload, dict) else {}
            if not isinstance(meta, dict):
                meta = {}
            try:
                total_pages = max(1, int(meta.get("totalPages") or meta.get("total_pages") or 1))
            except (TypeError, ValueError):
                total_pages = 1
            page += 1
    except Exception as exc:
        catalog_error = type(exc).__name__

    # Availability is authoritative per key. Even when the pricing catalog is
    # unavailable, keep the workflow usable and let the admin enter exact prices.
    missing_ids = allowed.difference(rows_by_id)
    if missing_ids or not rows_by_id:
        try:
            response = httpx.get(
                f"{base_url}/models",
                headers=headers,
                timeout=15,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data", []) if isinstance(payload, dict) else []
            for item in data:
                if not isinstance(item, dict):
                    continue
                model_id = str(item.get("id") or item.get("name") or "").strip()
                if not model_id:
                    continue
                if allowed and model_id not in allowed:
                    continue
                if model_id in rows_by_id:
                    continue
                row = _polza_price_row(item)
                rows_by_id[model_id] = {
                    "id": model_id,
                    "display_name": str(
                        item.get("display_name")
                        or item.get("displayName")
                        or item.get("name")
                        or model_id
                    ),
                    **row,
                    "pricing_available": any(
                        row.get(field) not in (None, "")
                        for field in (
                            "input_per_million",
                            "output_per_million",
                            "image_input_per_million",
                            "image_output_per_million",
                            "image_per_image",
                        )
                    ),
                }
        except Exception:
            # Last-resort manual workflow from the catalog saved during key health
            # check. No secret or price is fabricated.
            for model_id in sorted(allowed):
                rows_by_id.setdefault(
                    model_id,
                    {
                        "id": model_id,
                        "display_name": model_id,
                        "currency": "RUB",
                        "input_per_million": None,
                        "output_per_million": None,
                        "image_input_per_million": None,
                        "image_output_per_million": None,
                        "image_per_image": None,
                        "pricing_tiers": [],
                        "model_type": "",
                        "pricing_available": False,
                    },
                )

    # Procurement must never hide a model that the admin assigned to this key.
    # Pricing availability is optional; model visibility is not. Fill any missing
    # allowed model from the local registry / active PriceVersion so the admin can
    # still edit prices manually and complete the order.
    configured = {
        item.upstream_model: item
        for item in AIModel.objects.filter(provider=key.provider)
    }
    for model_id in sorted(allowed):
        if model_id in rows_by_id:
            continue
        model = configured.get(model_id)
        price = None
        if model is not None:
            price = (
                PriceVersion.objects.filter(model_slug=model.slug, active=True)
                .order_by("-effective_from", "-created_at")
                .first()
            )
        rows_by_id[model_id] = {
            "id": model_id,
            "display_name": (
                str(getattr(model, "display_name", "") or model_id)
                if model is not None
                else model_id
            ),
            "currency": str(getattr(price, "provider_currency", "") or "RUB"),
            "input_per_million": (
                str(price.input_price_per_million)
                if price is not None and price.input_price_per_million is not None
                else None
            ),
            "output_per_million": (
                str(price.output_price_per_million)
                if price is not None and price.output_price_per_million is not None
                else None
            ),
            "image_input_per_million": None,
            "image_output_per_million": None,
            "image_per_image": None,
            "pricing_tiers": [],
            "model_type": "",
            "pricing_available": bool(price is not None),
            "pricing_source": "local_price_version" if price is not None else "manual_required",
        }

    rows = list(rows_by_id.values())
    rows.sort(key=lambda item: (str(item.get("display_name") or "").casefold(), item["id"]))
    for row in rows:
        row["pricing_source"] = (
            "polza_catalog"
            if row.get("pricing_available")
            else "manual_required"
        )
        if catalog_error:
            row["catalog_warning"] = catalog_error
    return rows


def _pricing_snapshot_from_request(request, *, key, model=None, upstream_model=""):
    if key.provider.slug != "polza":
        return {}
    target_upstream = str(
        getattr(model, "upstream_model", "")
        or upstream_model
        or ""
    ).strip()
    if not target_upstream:
        return {}
    auto = {}
    try:
        rows = _polza_pricing_for_key(key)
        auto = next((item for item in rows if item["id"] == target_upstream), {})
    except Exception:
        auto = {}

    def chosen(field):
        raw = request.data.get(field)
        auto_value = auto.get(field)
        if raw not in (None, ""):
            value = _decimal(raw, field, minimum=ZERO)
            if auto_value not in (None, ""):
                try:
                    if Decimal(str(value)) == Decimal(str(auto_value)):
                        return str(value), "auto"
                except (InvalidOperation, TypeError, ValueError):
                    pass
            return str(value), "manual"
        if auto_value not in (None, ""):
            return str(auto_value), "auto"
        return None, "missing"

    input_price, input_source = chosen("input_per_million")
    output_price, output_source = chosen("output_per_million")
    image_input, image_input_source = chosen("image_input_per_million")
    image_output, image_output_source = chosen("image_output_per_million")
    image_per_image, image_per_image_source = chosen("image_per_image")
    currency = str(
        request.data.get("pricing_currency")
        or auto.get("currency")
        or "RUB"
    ).upper().strip()[:3] or "RUB"
    return {
        "provider": "polza",
        "api_key_id": str(key.id),
        "model_slug": getattr(model, "slug", "") or "",
        "upstream_model": target_upstream,
        "currency": currency,
        "input_per_million": input_price,
        "output_per_million": output_price,
        "image_input_per_million": image_input,
        "image_output_per_million": image_output,
        "image_per_image": image_per_image,
        "pricing_tiers": auto.get("pricing_tiers") or [],
        "model_type": auto.get("model_type") or "",
        "sources": {
            "input_per_million": input_source,
            "output_per_million": output_source,
            "image_input_per_million": image_input_source,
            "image_output_per_million": image_output_source,
            "image_per_image": image_per_image_source,
        },
        "source": "polza_models_catalog+manual_override",
        "captured_at": timezone.now().isoformat(),
    }


def _batch_polza_snapshot_from_request(request, *, key):
    raw_models = request.data.get("polza_models")
    if not isinstance(raw_models, list):
        return {}
    auto_rows = {item["id"]: item for item in _polza_pricing_for_key(key)}
    available = {str(value) for value in (key.available_models or []) if value}
    allowed = {str(value) for value in (key.allowed_models or []) if value}
    snapshots = []
    for raw in raw_models[:100]:
        if not isinstance(raw, dict):
            continue
        upstream = str(raw.get("upstream_model") or raw.get("id") or "").strip()[:160]
        if not upstream:
            continue
        if available and upstream not in available:
            raise DjangoValidationError(f"Модель {upstream} недоступна этому Polza API-ключу")
        if upstream not in allowed:
            raise DjangoValidationError(f"Модель {upstream} не разрешена для этого Polza API-ключа в AIlegend")
        auto = auto_rows.get(upstream, {})
        model = AIModel.objects.filter(provider=key.provider, upstream_model=upstream).first()

        def choose(field):
            raw_value = raw.get(field)
            auto_value = auto.get(field)
            if raw_value not in (None, ""):
                value = _decimal(raw_value, field, minimum=ZERO)
                source = "manual"
                if auto_value not in (None, ""):
                    try:
                        if Decimal(str(value)) == Decimal(str(auto_value)):
                            source = "auto"
                    except Exception:
                        pass
                return str(value), source
            if auto_value not in (None, ""):
                return str(auto_value), "auto"
            return None, "missing"

        input_price, input_source = choose("input_per_million")
        output_price, output_source = choose("output_per_million")
        image_input, image_input_source = choose("image_input_per_million")
        image_output, image_output_source = choose("image_output_per_million")
        image_per_image, image_per_image_source = choose("image_per_image")
        snapshot = {
            "model_slug": model.slug if model else "",
            "upstream_model": upstream,
            "display_name": str(raw.get("display_name") or auto.get("display_name") or upstream)[:200],
            "currency": str(raw.get("currency") or auto.get("currency") or "RUB").upper()[:3],
            "input_per_million": input_price,
            "output_per_million": output_price,
            "image_input_per_million": image_input,
            "image_output_per_million": image_output,
            "image_per_image": image_per_image,
            "pricing_tiers": auto.get("pricing_tiers") or [],
            "model_type": auto.get("model_type") or "",
            "sources": {
                "input_per_million": input_source,
                "output_per_million": output_source,
                "image_input_per_million": image_input_source,
                "image_output_per_million": image_output_source,
                "image_per_image": image_per_image_source,
            },
            "markup_percent": (
                str(_decimal(raw.get("markup_percent"), "Наценка модели", minimum=ZERO))
                if raw.get("markup_percent") not in (None, "")
                else None
            ),
        }
        _validate_polza_snapshot(snapshot)
        snapshots.append(snapshot)
    if not snapshots:
        raise DjangoValidationError("Для Polza выберите хотя бы одну модель и её цены")
    provider_markup = request.data.get("provider_markup_percent")
    provider_markup_value = (
        str(_decimal(provider_markup, "Общая наценка Polza", minimum=ZERO))
        if provider_markup not in (None, "")
        else None
    )
    return {
        "provider": "polza",
        "api_key_id": str(key.id),
        "currency": "RUB",
        "models": snapshots,
        "provider_markup_percent": provider_markup_value,
        "source": "polza_models_catalog+manual_override",
        "captured_at": timezone.now().isoformat(),
    }


def _apply_polza_markup_rules(*, provider, batch_snapshot):
    now = timezone.now()
    provider_markup = batch_snapshot.get("provider_markup_percent")
    if provider_markup not in (None, ""):
        MarkupRuleVersion.objects.create(
            scope_type=MarkupRuleVersion.Scope.PROVIDER,
            scope_key=provider.slug,
            markup_percent=Decimal(str(provider_markup)),
            price_multiplier=Decimal("1"),
            active=True,
            effective_from=now,
            reason="Polza procurement order: provider-wide markup",
        )
    for item in batch_snapshot.get("models") or []:
        model_slug = str(item.get("model_slug") or "").strip()
        markup = item.get("markup_percent")
        if not model_slug:
            continue
        MarkupRuleVersion.objects.create(
            scope_type=MarkupRuleVersion.Scope.MODEL,
            scope_key=model_slug,
            markup_percent=(
                Decimal(str(markup))
                if markup not in (None, "")
                else None
            ),
            price_multiplier=Decimal("1"),
            active=True,
            effective_from=now,
            reason=(
                "Polza procurement order: model markup override"
                if markup not in (None, "")
                else "Polza procurement order: reset model markup to provider rule"
            ),
        )


def _activate_polza_batch_prices(*, provider, batch_snapshot):
    for item in batch_snapshot.get("models") or []:
        model = None
        model_slug = str(item.get("model_slug") or "").strip()
        if model_slug:
            model = AIModel.objects.filter(provider=provider, slug=model_slug).first()
        if model is not None:
            price_version = _activate_polza_text_price(model=model, snapshot=item)
            if price_version is not None and model.current_version_id and not model.enabled:
                AIModel.objects.filter(pk=model.pk).update(enabled=True)
                model.enabled = True
        _activate_polza_image_price(
            provider=provider,
            upstream_model=str(item.get("upstream_model") or ""),
            snapshot=item,
        )
    _apply_polza_markup_rules(provider=provider, batch_snapshot=batch_snapshot)


def _validate_polza_snapshot(snapshot):
    if not snapshot:
        return
    input_price = snapshot.get("input_per_million")
    output_price = snapshot.get("output_per_million")
    image_per_image = snapshot.get("image_per_image")
    image_output = snapshot.get("image_output_per_million")
    if input_price in (None, "") and output_price in (None, "") and image_per_image in (None, "") and image_output in (None, ""):
        raise DjangoValidationError(
            "Polza не вернула цену выбранной модели. Укажите закупочную цену вручную."
        )
    if (input_price in (None, "")) != (output_price in (None, "")):
        raise DjangoValidationError(
            "Для текстовой Polza-модели укажите обе цены: запрос / 1М и ответ / 1М."
        )


def _activate_polza_image_price(*, provider, upstream_model, snapshot):
    price = snapshot.get("image_per_image")
    if price in (None, ""):
        return None
    try:
        from apps.image_studio.models import ImageModel
    except Exception:
        return None
    image_model = ImageModel.objects.filter(
        provider=provider,
        upstream_model=upstream_model,
    ).first()
    if image_model is None:
        return None
    value = Decimal(str(price))
    if value <= 0:
        return None
    image_model.provider_currency = str(snapshot.get("currency") or "RUB").upper()[:3]
    image_model.provider_price_per_image = value
    image_model.save(
        update_fields=["provider_currency", "provider_price_per_image"]
    )
    return image_model


def _activate_polza_text_price(*, model, snapshot):
    input_price = snapshot.get("input_per_million")
    output_price = snapshot.get("output_per_million")
    if input_price in (None, "") or output_price in (None, ""):
        return None
    currency = str(snapshot.get("currency") or "RUB").upper().strip()[:3]
    now = timezone.now()
    previous = (
        PriceVersion.objects.filter(model_slug=model.slug, active=True)
        .order_by("-effective_from", "-created_at")
        .first()
    )
    markup = previous.markup_percent if previous is not None else Decimal("100")
    PriceVersion.objects.filter(model_slug=model.slug, active=True).update(active=False)
    input_native = Decimal(str(input_price))
    output_native = Decimal(str(output_price))
    return PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=input_native if currency == "RUB" else Decimal("0.0001"),
        output_rub_per_million=output_native if currency == "RUB" else Decimal("0.0001"),
        provider_currency=currency,
        input_price_per_million=input_native,
        output_price_per_million=output_native,
        markup_percent=markup,
        active=True,
        effective_from=now,
    )


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
        "pricing_snapshot": purchase.pricing_snapshot or {},
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

    pricing_snapshot = purchase.pricing_snapshot or {}
    selected_model = None
    if account.provider.slug == "polza" and account.api_key_id:
        selected_upstream = str(
            request.data.get("polza_model_id")
            or pricing_snapshot.get("upstream_model")
            or ""
        ).strip()
        selected_model_slug = str(
            request.data.get("model_slug")
            or pricing_snapshot.get("model_slug")
            or ""
        ).strip()
        if selected_upstream:
            selected_model = (
                AIModel.objects.filter(
                    provider=account.provider,
                    upstream_model=selected_upstream,
                )
                .select_related("provider")
                .first()
            )
            if selected_model_slug and selected_model is None:
                selected_model = (
                    AIModel.objects.filter(
                        provider=account.provider,
                        slug=selected_model_slug,
                    )
                    .select_related("provider")
                    .first()
                )
            allowed = list(account.api_key.allowed_models or [])
            if selected_upstream not in allowed:
                raise DjangoValidationError(
                    "Выбранная модель не разрешена для этого Polza API-ключа"
                )
            pricing_snapshot = _pricing_snapshot_from_request(
                request,
                key=account.api_key,
                model=selected_model,
                upstream_model=selected_upstream,
            )
            _validate_polza_snapshot(pricing_snapshot)

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
        "pricing_snapshot": pricing_snapshot,
        "reference": str(request.data.get("reference") or "")[:300],
        "purchased_at": _purchase_time(request.data.get("purchased_at")),
        "updated_at": timezone.now(),
    }
    ProviderPurchase.objects.filter(pk=purchase.pk).update(**fields)
    purchase.refresh_from_db()
    if pricing_snapshot:
        if selected_model is not None:
            _activate_polza_text_price(model=selected_model, snapshot=pricing_snapshot)
        _activate_polza_image_price(
            provider=account.provider,
            upstream_model=str(pricing_snapshot.get("upstream_model") or ""),
            snapshot=pricing_snapshot,
        )
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


def _polza_vendor(upstream_model):
    value = str(upstream_model or "").strip()
    if "/" in value:
        return value.split("/", 1)[0].lower()
    return "other"


def _polza_budget_status(key, account):
    plan = list(key.budget_plan or [])
    if not plan:
        return []
    model_map = {
        item.slug: item.upstream_model
        for item in AIModel.objects.filter(provider=key.provider)
    }
    spent_by_vendor = {}
    spent_by_model = {}
    if account is not None:
        for spend in account.spends.all().only("model_slug", "nominal_cost_rub"):
            upstream = model_map.get(spend.model_slug, "")
            vendor = _polza_vendor(upstream)
            spent_by_vendor[vendor] = spent_by_vendor.get(vendor, ZERO) + spend.nominal_cost_rub
            if upstream:
                spent_by_model[upstream] = spent_by_model.get(upstream, ZERO) + spend.nominal_cost_rub
    result = []
    for item in plan:
        scope_type = str(item.get("scope_type") or "vendor")
        scope_key = str(item.get("scope_key") or "")
        allocated = _decimal_or_none(item.get("allocated_rub")) or ZERO
        spent = spent_by_model.get(scope_key, ZERO) if scope_type == "model" else spent_by_vendor.get(scope_key, ZERO)
        result.append({
            **item,
            "allocated_rub": str(allocated),
            "spent_rub": str(spent.quantize(MONEY)),
            "remaining_rub": str(max(ZERO, allocated - spent).quantize(MONEY)),
        })
    return result


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
                "model_scope_source": key.model_scope_source,
                "allowed_models": list(key.allowed_models or []),
                "budget_plan": list(key.budget_plan or []),
                "budget_status": _polza_budget_status(key, account) if key.provider.slug == "polza" else [],
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
            if action == "polza_pricing":
                key = ProviderApiKey.objects.select_related("provider").get(
                    pk=request.data.get("api_key_id"),
                    provider__slug="polza",
                )
                rows = _polza_pricing_for_key(key)
                configured = {
                    item.upstream_model: item
                    for item in AIModel.objects.filter(provider=key.provider)
                }
                allowed = {str(value) for value in (key.allowed_models or []) if value}
                rows = [
                    {
                        **item,
                        "model_slug": configured[item["id"]].slug if item["id"] in configured else None,
                        "configured": item["id"] in configured,
                        "allowed": item["id"] in allowed,
                    }
                    for item in rows
                ]
                requested_model = str(request.data.get("model_id") or "").strip()
                if requested_model:
                    row = next((item for item in rows if item["id"] == requested_model), None)
                    if row is None:
                        return Response({"detail": "Модель недоступна для этого Polza-ключа"}, status=404)
                    return Response({"provider": "polza", "model": row})
                return Response({"provider": "polza", "models": rows})
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
            credit_currency = str(
                request.data.get("credit_currency")
                or provider_pricing_currency(key.provider, key.balance_currency or "USD")
            ).upper().strip()
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

            selected_model = None
            pricing_snapshot = {}
            selected_upstream = str(request.data.get("polza_model_id") or "").strip()
            selected_model_slug = str(request.data.get("model_slug") or "").strip()
            if key.provider.slug == "polza":
                if isinstance(request.data.get("polza_models"), list):
                    pricing_snapshot = _batch_polza_snapshot_from_request(request, key=key)
                elif selected_upstream:
                    selected_model = (
                        AIModel.objects.filter(provider=key.provider, upstream_model=selected_upstream)
                        .select_related("provider")
                        .first()
                    )
                    if selected_model_slug and selected_model is None:
                        selected_model = (
                            AIModel.objects.filter(provider=key.provider, slug=selected_model_slug)
                            .select_related("provider")
                            .first()
                        )
                    allowed = list(key.allowed_models or [])
                    if selected_upstream not in allowed:
                        raise DjangoValidationError(
                            "Выбранная модель не разрешена для этого Polza API-ключа"
                        )
                    pricing_snapshot = _pricing_snapshot_from_request(
                        request,
                        key=key,
                        model=selected_model,
                        upstream_model=selected_upstream,
                    )
                    _validate_polza_snapshot(pricing_snapshot)

            if key.provider.slug == "polza" and isinstance(request.data.get("budget_plan"), list):
                plan = []
                total_allocated = ZERO
                for raw in request.data.get("budget_plan")[:100]:
                    if not isinstance(raw, dict):
                        continue
                    scope_type = str(raw.get("scope_type") or "vendor").strip()
                    if scope_type not in {"vendor", "model"}:
                        raise DjangoValidationError("Некорректный тип бюджета Polza")
                    scope_key = str(raw.get("scope_key") or "").strip()[:160]
                    if not scope_key:
                        continue
                    allocated = _decimal(raw.get("allocated_rub"), "Бюджет Polza", minimum=ZERO) or ZERO
                    if allocated <= ZERO:
                        continue
                    total_allocated += allocated
                    plan.append({
                        "scope_type": scope_type,
                        "scope_key": scope_key,
                        "label": str(raw.get("label") or scope_key)[:120],
                        "allocated_rub": str(allocated),
                    })
                credit_for_check = _decimal(request.data.get("credit_native"), "Номинал API-баланса", required=True, minimum=Decimal("0.000001"))
                if credit_for_check is not None and credit_currency == "RUB" and total_allocated > credit_for_check:
                    raise DjangoValidationError("Сумма внутренних бюджетов Polza превышает номинал ключа")
                key.budget_plan = plan
                key.save(update_fields=["budget_plan"])

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
                pricing_snapshot=pricing_snapshot,
                reference=request.data.get("reference") or "",
            )
            if pricing_snapshot:
                if isinstance(pricing_snapshot.get("models"), list):
                    _activate_polza_batch_prices(
                        provider=key.provider,
                        batch_snapshot=pricing_snapshot,
                    )
                else:
                    if selected_model is not None:
                        _activate_polza_text_price(
                            model=selected_model,
                            snapshot=pricing_snapshot,
                        )
                    _activate_polza_image_price(
                        provider=key.provider,
                        upstream_model=str(pricing_snapshot.get("upstream_model") or ""),
                        snapshot=pricing_snapshot,
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
