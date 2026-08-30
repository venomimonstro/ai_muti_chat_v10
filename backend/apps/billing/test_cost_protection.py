from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider

from .models import (
    BillingReconciliationItem,
    CostAnomaly,
    FxRateSnapshot,
    MarginPolicyVersion,
    MarkupRuleVersion,
    PriceVersion,
    RequestCost,
)
from .pricing import calculate_from_snapshot, quote, require_margin
from .reconciliation import reconcile_billing, record_cost_outcome
from .services import credit


def usd_price(slug="cost-model"):
    return PriceVersion.objects.create(
        model_slug=slug,
        input_rub_per_million=0,
        output_rub_per_million=0,
        provider_currency="USD",
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("2"),
        markup_percent=100,
        effective_from=timezone.now(),
    )


def fx(rate, source):
    return FxRateSnapshot.objects.create(
        base_currency="USD",
        quote_currency="RUB",
        rate=Decimal(str(rate)),
        source=source,
        effective_at=timezone.now(),
    )


@pytest.mark.django_db
def test_markup_hierarchy_and_contract_multiplier_are_snapshotted():
    fx(80, "test")
    price = usd_price()
    for scope, key, markup, multiplier in [
        (MarkupRuleVersion.Scope.PROVIDER, "google", 80, 1),
        (MarkupRuleVersion.Scope.MODEL, "cost-model", 60, 1),
        (MarkupRuleVersion.Scope.OPERATION, "chat", 50, 1),
        (MarkupRuleVersion.Scope.ORGANIZATION, "org-1", 40, 1),
        (MarkupRuleVersion.Scope.CONTRACT, "contract-1", None, Decimal("1.1")),
    ]:
        MarkupRuleVersion.objects.create(
            scope_type=scope,
            scope_key=key,
            markup_percent=markup,
            price_multiplier=multiplier,
            effective_from=timezone.now(),
        )
    value = quote(
        price,
        1_000_000,
        0,
        provider_slug="google",
        model_slug="cost-model",
        organization_id="org-1",
        contract_id="contract-1",
    )
    assert value.provider_cost_rub == Decimal("80.0000")
    assert value.user_charge_rub == Decimal("123.2000")
    assert value.pricing_snapshot["effective_markup_percent"] == "40.000"
    assert value.pricing_snapshot["price_multiplier"] == "1.1000"
    assert [item["scope_type"] for item in value.pricing_snapshot["markup_rules"]][-2:] == [
        "organization",
        "contract",
    ]


@pytest.mark.django_db
def test_fx_and_price_snapshot_reproduce_history_after_new_rates_and_rules():
    first_fx = fx(80, "first")
    price = usd_price("snapshot-model")
    first = quote(price, 1_000_000, 0, model_slug="snapshot-model")
    fx(100, "second")
    MarkupRuleVersion.objects.create(
        scope_type=MarkupRuleVersion.Scope.MODEL,
        scope_key="snapshot-model",
        markup_percent=200,
        effective_from=timezone.now(),
    )
    current = quote(price, 1_000_000, 0, model_slug="snapshot-model")
    reproduced = calculate_from_snapshot(price, 1_000_000, 0, first.pricing_snapshot)
    assert first.fx_snapshot == first_fx
    assert first.user_charge_rub == Decimal("160.0000")
    assert current.user_charge_rub == Decimal("300.0000")
    assert reproduced[:2] == (Decimal("80.0000"), Decimal("160.0000"))
    first_fx.rate = Decimal("999")
    with pytest.raises(ValidationError):
        first_fx.save()


@pytest.mark.django_db
def test_margin_floor_blocks_unprofitable_quote():
    MarginPolicyVersion.objects.create(
        minimum_gross_margin_percent=60,
        anomaly_cost_deviation_percent=20,
        reconciliation_threshold_rub=1,
        effective_from=timezone.now(),
    )
    price = PriceVersion.objects.create(
        model_slug="floor-model",
        input_rub_per_million=100,
        output_rub_per_million=100,
        markup_percent=100,
        effective_from=timezone.now(),
    )
    value = quote(price, 1_000_000, 0, model_slug="floor-model")
    assert value.gross_margin_percent == Decimal("50.000")
    assert value.margin_allowed is False
    with pytest.raises(ValidationError):
        require_margin(value)


@pytest.mark.django_db
def test_cost_outcome_creates_deduplicated_margin_and_deviation_alerts():
    provider = Provider.objects.create(slug="cost-provider", name="Cost")
    model = AIModel.objects.create(
        provider=provider,
        slug="alert-model",
        display_name="Alert",
        upstream_model="alert-exact",
    )
    price = PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=100,
        output_rub_per_million=100,
        effective_from=timezone.now(),
    )
    request_cost = RequestCost.objects.create(
        generation_id=model.id,
        price_version=price,
        estimated_rub=2,
        expected_provider_cost_rub=Decimal("1"),
        provider_cost_rub=Decimal("2"),
        charged_rub=Decimal("2.2"),
        gross_margin_percent=Decimal("9.091"),
    )
    record_cost_outcome(request_cost, model=model)
    record_cost_outcome(request_cost, model=model)
    assert CostAnomaly.objects.filter(request_cost=request_cost).count() == 2
    assert set(CostAnomaly.objects.values_list("kind", flat=True)) == {
        CostAnomaly.Kind.MARGIN_FLOOR,
        CostAnomaly.Kind.COST_DEVIATION,
    }


@pytest.mark.django_db(transaction=True)
def test_reconciliation_detects_cached_wallet_ledger_mismatch_without_rewriting_money():
    user = User.objects.create_user(username="reconcile", password="password123")
    credit(user, Decimal("10"), "test", "reconcile")
    user.wallet.available_rub = Decimal("9")
    user.wallet.save(update_fields=["available_rub"])
    run = reconcile_billing()
    user.wallet.refresh_from_db()
    item = BillingReconciliationItem.objects.get(run=run, entity_type="wallet")
    assert run.status == run.Status.SUCCEEDED
    assert run.discrepancy_count == 1
    assert item.status == BillingReconciliationItem.Status.MANUAL_REVIEW
    assert user.wallet.available_rub == Decimal("9")
    assert CostAnomaly.objects.filter(kind=CostAnomaly.Kind.LEDGER_MISMATCH).count() == 1


@pytest.mark.django_db
def test_finance_summary_is_staff_only():
    user = User.objects.create_user(
        username="finance-user", email="finance-user@example.com", password="password123"
    )
    staff = User.objects.create_user(
        username="finance-staff",
        email="finance-staff@example.com",
        password="password123",
        is_staff=True,
        role=User.Role.PLATFORM_ADMIN,
    )
    client = APIClient()
    client.force_authenticate(user)
    assert client.get("/api/v1/finance/summary/").status_code == 403
    client.force_authenticate(staff)
    response = client.get("/api/v1/finance/summary/")
    assert response.status_code == 200
    assert response.data["open_anomalies"] == 0
