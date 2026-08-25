from dataclasses import dataclass
from decimal import ROUND_UP, Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import FxRateSnapshot, MarginPolicyVersion, MarkupRuleVersion, PriceVersion

MILLION = Decimal("1000000")
MONEY_STEP = Decimal("0.0001")
PERCENT_STEP = Decimal("0.001")


class MarginFloorError(ValidationError):
    pass


@dataclass(frozen=True)
class PriceQuote:
    provider_cost_rub: Decimal
    user_charge_rub: Decimal
    gross_profit_rub: Decimal
    gross_margin_percent: Decimal
    margin_floor_percent: Decimal
    margin_allowed: bool
    fx_snapshot: FxRateSnapshot | None
    pricing_snapshot: dict


def active_price(model_slug: str) -> PriceVersion:
    price = (
        PriceVersion.objects.filter(
            model_slug=model_slug, active=True, effective_from__lte=timezone.now()
        )
        .order_by("-effective_from", "-created_at")
        .first()
    )
    if not price:
        raise ValidationError("Для выбранной модели не настроена цена")
    return price


def active_fx_snapshot(currency: str) -> FxRateSnapshot | None:
    currency = currency.upper()
    snapshot = (
        FxRateSnapshot.objects.filter(
            base_currency=currency,
            quote_currency="RUB",
            effective_at__lte=timezone.now(),
        )
        .order_by("-effective_at", "-created_at")
        .first()
    )
    if not snapshot and currency == "RUB":
        snapshot = FxRateSnapshot.objects.create(
            base_currency="RUB",
            quote_currency="RUB",
            rate=Decimal("1"),
            source="system_identity",
            effective_at=timezone.now(),
        )
    if not snapshot:
        raise ValidationError(f"Не настроен FX snapshot {currency}/RUB")
    return snapshot


def active_margin_policy() -> MarginPolicyVersion:
    policy = (
        MarginPolicyVersion.objects.filter(active=True, effective_from__lte=timezone.now())
        .order_by("-effective_from", "-created_at")
        .first()
    )
    if not policy:
        policy = MarginPolicyVersion.objects.create(
            minimum_gross_margin_percent=25,
            anomaly_cost_deviation_percent=20,
            reconciliation_threshold_rub=1,
            effective_from=timezone.now(),
        )
    return policy


def _latest_rule(scope_type, scope_key=""):
    rule = (
        MarkupRuleVersion.objects.filter(
            scope_type=scope_type,
            scope_key=scope_key,
            active=True,
            effective_from__lte=timezone.now(),
        )
        .order_by("-effective_from", "-created_at")
        .first()
    )
    if not rule and scope_type == MarkupRuleVersion.Scope.GLOBAL:
        rule = MarkupRuleVersion.objects.create(
            scope_type=MarkupRuleVersion.Scope.GLOBAL,
            markup_percent=100,
            effective_from=timezone.now(),
            reason="Default global markup",
        )
    return rule


def _effective_rules(
    *,
    price,
    provider_slug="",
    model_slug="",
    operation_type="chat",
    organization_id=None,
    contract_id=None,
):
    markup = price.markup_percent
    multiplier = Decimal("1")
    applied = []
    scopes = [
        (MarkupRuleVersion.Scope.GLOBAL, ""),
        (MarkupRuleVersion.Scope.PROVIDER, provider_slug),
        (MarkupRuleVersion.Scope.MODEL, model_slug or price.model_slug),
        (MarkupRuleVersion.Scope.OPERATION, operation_type),
        (MarkupRuleVersion.Scope.ORGANIZATION, str(organization_id or "")),
        (MarkupRuleVersion.Scope.CONTRACT, str(contract_id or "")),
    ]
    for scope_type, scope_key in scopes:
        if scope_type != MarkupRuleVersion.Scope.GLOBAL and not scope_key:
            continue
        rule = _latest_rule(scope_type, scope_key)
        if not rule:
            continue
        if rule.markup_percent is not None:
            markup = rule.markup_percent
        multiplier *= rule.price_multiplier
        applied.append(
            {
                "id": str(rule.id),
                "scope_type": rule.scope_type,
                "scope_key": rule.scope_key,
                "markup_percent": (
                    str(rule.markup_percent) if rule.markup_percent is not None else None
                ),
                "price_multiplier": str(rule.price_multiplier),
            }
        )
    return markup, multiplier, applied


def _native_cost(price, input_tokens, output_tokens):
    input_price = (
        price.input_price_per_million
        if price.input_price_per_million is not None
        else price.input_rub_per_million
    )
    output_price = (
        price.output_price_per_million
        if price.output_price_per_million is not None
        else price.output_rub_per_million
    )
    return (
        Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price
    ) / MILLION


def quote(
    price: PriceVersion,
    input_tokens: int,
    output_tokens: int,
    *,
    provider_slug="",
    model_slug="",
    operation_type="chat",
    organization_id=None,
    contract_id=None,
):
    fx = active_fx_snapshot(price.provider_currency)
    provider_cost = (_native_cost(price, input_tokens, output_tokens) * fx.rate).quantize(
        MONEY_STEP, rounding=ROUND_UP
    )
    markup, multiplier, rules = _effective_rules(
        price=price,
        provider_slug=provider_slug,
        model_slug=model_slug,
        operation_type=operation_type,
        organization_id=organization_id,
        contract_id=contract_id,
    )
    charge = (
        provider_cost * (Decimal("1") + markup / Decimal("100")) * multiplier
    ).quantize(MONEY_STEP, rounding=ROUND_UP)
    profit = (charge - provider_cost).quantize(MONEY_STEP)
    margin = (
        (profit / charge * Decimal("100")).quantize(PERCENT_STEP)
        if charge
        else Decimal("100.000")
    )
    policy = active_margin_policy()
    snapshot = {
        "price_version_id": str(price.id),
        "provider_currency": price.provider_currency,
        "fx_snapshot_id": str(fx.id),
        "fx_rate": str(fx.rate),
        "effective_markup_percent": str(markup),
        "price_multiplier": str(multiplier.quantize(Decimal("0.0001"))),
        "markup_rules": rules,
        "margin_policy_id": str(policy.id),
        "margin_floor_percent": str(policy.minimum_gross_margin_percent),
        "operation_type": operation_type,
    }
    return PriceQuote(
        provider_cost_rub=provider_cost,
        user_charge_rub=charge,
        gross_profit_rub=profit,
        gross_margin_percent=margin,
        margin_floor_percent=policy.minimum_gross_margin_percent,
        margin_allowed=margin >= policy.minimum_gross_margin_percent,
        fx_snapshot=fx,
        pricing_snapshot=snapshot,
    )


def calculate(price: PriceVersion, input_tokens: int, output_tokens: int):
    value = quote(price, input_tokens, output_tokens, model_slug=price.model_slug)
    return value.provider_cost_rub, value.user_charge_rub


def calculate_from_snapshot(price, input_tokens, output_tokens, snapshot):
    fx_rate = Decimal(snapshot["fx_rate"])
    markup = Decimal(snapshot["effective_markup_percent"])
    multiplier = Decimal(snapshot["price_multiplier"])
    provider_cost = (_native_cost(price, input_tokens, output_tokens) * fx_rate).quantize(
        MONEY_STEP, rounding=ROUND_UP
    )
    charge = (
        provider_cost * (Decimal("1") + markup / Decimal("100")) * multiplier
    ).quantize(MONEY_STEP, rounding=ROUND_UP)
    profit = (charge - provider_cost).quantize(MONEY_STEP)
    margin = (
        (profit / charge * Decimal("100")).quantize(PERCENT_STEP)
        if charge
        else Decimal("100.000")
    )
    return provider_cost, charge, profit, margin


def require_margin(value: PriceQuote):
    if not value.margin_allowed:
        raise MarginFloorError(
            f"Ожидаемая маржа {value.gross_margin_percent}% ниже floor "
            f"{value.margin_floor_percent}%"
        )
    return value


def conservative_token_budget(messages: list[dict], max_output_tokens: int):
    # One Unicode character per token is deliberately conservative until a model tokenizer is added.
    input_budget = sum(len(item.get("content", "")) for item in messages) + 32
    return input_budget, max_output_tokens
