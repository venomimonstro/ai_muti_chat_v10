from dataclasses import dataclass
from decimal import ROUND_UP, Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.ai_registry.models import AIModel

from .models import CostAnomaly, FxRateSnapshot, PriceVersion

RUB_STEP = Decimal("0.0001")


@dataclass(frozen=True)
class PriceChange:
    model_slug: str
    changed: bool
    input_change_percent: Decimal
    output_change_percent: Decimal
    applied: bool


def _decimal(value, field):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid decimal: {field}") from exc
    if result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


def _percent(old, new):
    if old is None or old == 0:
        return Decimal("100")
    return abs((new - old) / old * Decimal("100"))


def _active_fx_rate(currency: str, at_time) -> Decimal:
    currency = currency.upper()
    if currency == "RUB":
        return Decimal("1")
    snapshot = (
        FxRateSnapshot.objects.filter(
            base_currency=currency,
            quote_currency="RUB",
            effective_at__lte=at_time,
        )
        .order_by("-effective_at", "-created_at")
        .first()
    )
    if snapshot is None:
        raise ValueError(f"Missing FX snapshot {currency}/RUB")
    return snapshot.rate


@transaction.atomic
def sync_pricing_catalog(payload: dict, *, apply=False, anomaly_threshold_percent=Decimal("10")):
    now = timezone.now()
    source = str(payload.get("source") or "reviewed_catalog")[:80]
    source_reference = str(payload.get("source_reference") or "")[:240]
    fx_created = 0
    for item in payload.get("fx", []):
        base = str(item["base_currency"]).upper()
        quote_currency = str(item.get("quote_currency") or "RUB").upper()
        if quote_currency != "RUB":
            raise ValueError("Only RUB quote currency is supported")
        rate = _decimal(item["rate"], "fx.rate")
        effective_at = item.get("effective_at") or now
        _, created = FxRateSnapshot.objects.get_or_create(
            base_currency=base,
            quote_currency=quote_currency,
            source=source,
            effective_at=effective_at,
            defaults={"rate": rate, "source_reference": source_reference},
        )
        fx_created += int(created)

    changes = []
    for item in payload.get("models", []):
        model_slug = str(item["model_slug"])
        model = AIModel.objects.select_related("provider").filter(slug=model_slug).first()
        if model is None:
            raise ValueError(f"Unknown model: {model_slug}")
        currency = str(item.get("provider_currency") or "USD").upper()
        input_native = _decimal(item["input_price_per_million"], "input_price_per_million")
        output_native = _decimal(item["output_price_per_million"], "output_price_per_million")
        current = (
            PriceVersion.objects.filter(model_slug=model_slug, active=True)
            .order_by("-effective_from", "-created_at")
            .first()
        )
        old_input = current.input_price_per_million if current else None
        old_output = current.output_price_per_million if current else None
        input_change = _percent(old_input, input_native)
        output_change = _percent(old_output, output_native)
        changed = (
            current is None
            or current.provider_currency != currency
            or old_input != input_native
            or old_output != output_native
        )
        if changed and max(input_change, output_change) >= anomaly_threshold_percent:
            CostAnomaly.objects.get_or_create(
                dedupe_key=f"provider-price:{model_slug}:{input_native}:{output_native}:{currency}",
                defaults={
                    "kind": CostAnomaly.Kind.COST_DEVIATION,
                    "severity": "warning",
                    "model_slug": model_slug,
                    "provider_slug": model.provider.slug,
                    "details": {
                        "source": source,
                        "source_reference": source_reference,
                        "old_input": str(old_input) if old_input is not None else None,
                        "new_input": str(input_native),
                        "old_output": str(old_output) if old_output is not None else None,
                        "new_output": str(output_native),
                        "currency": currency,
                        "input_change_percent": str(input_change),
                        "output_change_percent": str(output_change),
                    },
                },
            )
        applied = False
        if changed and apply:
            fx_rate = _active_fx_rate(currency, now)
            input_rub = (input_native * fx_rate).quantize(RUB_STEP, rounding=ROUND_UP)
            output_rub = (output_native * fx_rate).quantize(RUB_STEP, rounding=ROUND_UP)
            if current is not None:
                PriceVersion.objects.filter(pk=current.pk).update(active=False)
            PriceVersion.objects.create(
                model_slug=model_slug,
                input_rub_per_million=input_rub,
                output_rub_per_million=output_rub,
                provider_currency=currency,
                input_price_per_million=input_native,
                output_price_per_million=output_native,
                markup_percent=current.markup_percent if current else Decimal("100"),
                active=True,
                effective_from=now,
            )
            applied = True
        changes.append(
            PriceChange(
                model_slug=model_slug,
                changed=changed,
                input_change_percent=input_change,
                output_change_percent=output_change,
                applied=applied,
            )
        )
    return {"fx_created": fx_created, "changes": changes}
