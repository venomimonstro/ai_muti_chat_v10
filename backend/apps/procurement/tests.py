from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import FxRateSnapshot, MarginPolicyVersion, PriceVersion, RequestCost
from apps.billing.pricing import quote

from .models import ProviderFundingAccount, ProviderSpend, ProviderSpendReservation, RetailTokenPriceVersion
from .services import (
    account_weighted_unit_cost_rub,
    create_funding_account,
    record_purchase,
    reserve_provider_spend,
    settle_provider_spend,
)


@pytest.fixture
def procurement_context(monkeypatch):
    user = User.objects.create_user(username="owner", email="owner@example.test", password="password123")
    provider = Provider.objects.create(
        slug="vendor",
        name="Vendor",
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
        credential_env="VENDOR_API_KEY_1",
    )
    model = AIModel.objects.create(
        provider=provider,
        slug="vendor-model",
        display_name="Vendor model",
        upstream_model="vendor-model-v1",
        capabilities=["text"],
    )
    account = create_funding_account(
        provider=provider,
        label="Основной",
        credential_env="VENDOR_API_KEY_1",
        currency="USD",
        low_balance_native=Decimal("2"),
        is_default=True,
    )
    monkeypatch.setenv("VENDOR_API_KEY_1", "secret-for-test")
    return user, provider, model, account


@pytest.mark.django_db(transaction=True)
def test_purchase_fees_are_included_in_real_acquisition_cost(procurement_context):
    user, _provider, _model, account = procurement_context
    purchase = record_purchase(
        account=account,
        credit_native=Decimal("10"),
        base_cost_rub=Decimal("1000"),
        fees_rub=Decimal("1000"),
        purchased_at=timezone.now(),
        created_by=user,
        market_fx_rate_rub=Decimal("100"),
    )
    account.refresh_from_db()
    assert purchase.total_cash_outlay_rub == Decimal("2000.0000")
    assert account.funded_native == Decimal("10.000000")
    assert account_weighted_unit_cost_rub(account) == Decimal("200.00000000")


@pytest.mark.django_db(transaction=True)
def test_provider_credit_cannot_be_reserved_above_purchased_balance(procurement_context):
    user, provider, _model, account = procurement_context
    record_purchase(
        account=account,
        credit_native=Decimal("10"),
        base_cost_rub=Decimal("1000"),
        fees_rub=Decimal("0"),
        purchased_at=timezone.now(),
        created_by=user,
    )
    reserve_provider_spend(provider=provider, amount_native=Decimal("9"), source_key="first")
    with pytest.raises(ValidationError, match="исчерпан"):
        reserve_provider_spend(provider=provider, amount_native=Decimal("2"), source_key="second")
    account.refresh_from_db()
    assert account.reserved_native == Decimal("9.000000")
    assert account.available_native == Decimal("1.000000")


@pytest.mark.django_db(transaction=True)
def test_provider_spend_settlement_uses_purchase_cost_including_fees(procurement_context):
    user, provider, model, account = procurement_context
    record_purchase(
        account=account,
        credit_native=Decimal("10"),
        base_cost_rub=Decimal("1000"),
        fees_rub=Decimal("1000"),
        purchased_at=timezone.now(),
        created_by=user,
    )
    reservation = reserve_provider_spend(
        provider=provider,
        amount_native=Decimal("2"),
        source_key="chat:test",
    )
    spend = settle_provider_spend(
        reservation_id=reservation.id,
        actual_native=Decimal("1.5"),
        nominal_cost_rub=Decimal("150"),
        source_type="chat",
        source_id="test",
        model_slug=model.slug,
        input_tokens=100,
        output_tokens=50,
    )
    account.refresh_from_db()
    assert spend.economic_cost_rub == Decimal("300.0000")
    assert account.spent_native == Decimal("1.500000")
    assert account.reserved_native == Decimal("0.000000")


@pytest.mark.django_db(transaction=True)
def test_explicit_retail_prices_control_real_quote_and_keep_margin_floor(procurement_context):
    user, provider, model, _account = procurement_context
    FxRateSnapshot.objects.create(
        base_currency="USD", quote_currency="RUB", rate=Decimal("100"), source="test", effective_at=timezone.now()
    )
    MarginPolicyVersion.objects.create(
        minimum_gross_margin_percent=Decimal("25"),
        anomaly_cost_deviation_percent=Decimal("20"),
        reconciliation_threshold_rub=Decimal("1"),
        effective_from=timezone.now(),
    )
    price = PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("50"),
        output_rub_per_million=Decimal("200"),
        provider_currency="USD",
        input_price_per_million=Decimal("0.5"),
        output_price_per_million=Decimal("2"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    RetailTokenPriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("120"),
        output_rub_per_million=Decimal("500"),
        effective_from=timezone.now(),
        created_by=user,
    )
    value = quote(price, 1_000_000, 1_000_000, provider_slug=provider.slug, model_slug=model.slug)
    assert value.provider_cost_rub == Decimal("250.0000")
    assert value.user_charge_rub == Decimal("620.0000")
    assert value.gross_profit_rub == Decimal("370.0000")
    assert value.margin_allowed is True
    assert value.pricing_snapshot["pricing_mode"] == "retail_token"


@pytest.mark.django_db(transaction=True)
def test_request_cost_signal_reserves_and_settles_vendor_balance(procurement_context):
    user, provider, model, account = procurement_context
    record_purchase(
        account=account,
        credit_native=Decimal("10"),
        base_cost_rub=Decimal("1000"),
        fees_rub=Decimal("0"),
        purchased_at=timezone.now(),
        created_by=user,
    )
    fx = FxRateSnapshot.objects.create(
        base_currency="USD", quote_currency="RUB", rate=Decimal("100"), source="test-signal", effective_at=timezone.now()
    )
    price = PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("50"),
        output_rub_per_million=Decimal("200"),
        provider_currency="USD",
        input_price_per_million=Decimal("0.5"),
        output_price_per_million=Decimal("2"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    cost = RequestCost.objects.create(
        generation_id=model.id,
        price_version=price,
        estimated_rub=Decimal("4"),
        expected_provider_cost_rub=Decimal("2"),
        fx_snapshot=fx,
        pricing_snapshot={"fx_rate": "100", "pricing_mode": "markup", "effective_markup_percent": "100", "price_multiplier": "1"},
    )
    reservation = ProviderSpendReservation.objects.get(source_key=f"chat:{cost.id}:{price.id}")
    assert reservation.amount_native == Decimal("0.020000")
    account.refresh_from_db()
    assert account.reserved_native == Decimal("0.020000")

    cost.provider_cost_rub = Decimal("1.5000")
    cost.charged_rub = Decimal("3.0000")
    cost.input_tokens = 100
    cost.output_tokens = 50
    cost.save(update_fields=["provider_cost_rub", "charged_rub", "input_tokens", "output_tokens"])
    account.refresh_from_db()
    assert account.reserved_native == Decimal("0.000000")
    assert account.spent_native == Decimal("0.015000")
    assert ProviderSpend.objects.filter(source_type="chat", source_id=str(cost.id)).exists()


@pytest.mark.django_db(transaction=True)
def test_procurement_overrun_disables_provider_without_charging_beyond_funding(procurement_context):
    user, provider, model, account = procurement_context
    record_purchase(
        account=account,
        credit_native=Decimal("1"),
        base_cost_rub=Decimal("100"),
        fees_rub=Decimal("0"),
        purchased_at=timezone.now(),
        created_by=user,
    )
    reservation = reserve_provider_spend(provider=provider, amount_native=Decimal("0.1"), source_key="overrun")
    result = settle_provider_spend(
        reservation_id=reservation.id,
        actual_native=Decimal("0.2"),
        nominal_cost_rub=Decimal("20"),
        source_type="chat",
        source_id="overrun",
        model_slug=model.slug,
    )
    assert result is None
    provider.refresh_from_db()
    account.refresh_from_db()
    assert provider.emergency_disabled is True
    assert account.spent_native == Decimal("0.000000")
    assert account.reserved_native == Decimal("0.000000")


@pytest.mark.django_db(transaction=True)
def test_procurement_safety_gate_blocks_empty_provider_balance(procurement_context):
    _user, _provider, _model, _account = procurement_context
    with pytest.raises(CommandError, match="provider_procurement_balance_empty"):
        call_command("procurement_safety_check")
