from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.billing.models import CostAnomaly

from .models import ProviderSpend
from .services import (
    create_funding_account,
    record_purchase,
    reserve_provider_spend,
    settle_provider_spend,
)


@pytest.mark.django_db(transaction=True)
def test_provider_overrun_is_persisted_and_provider_is_disabled(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "secret")
    user = User.objects.create_user(username="procurement-overrun-admin")
    provider = Provider.objects.create(
        slug="procurement-overrun-provider",
        name="Procurement overrun provider",
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
        credential_env="TEST_PROVIDER_API_KEY",
    )
    account = create_funding_account(
        provider=provider,
        label="main",
        credential_env="TEST_PROVIDER_API_KEY",
        currency="USD",
        is_default=True,
    )
    record_purchase(
        account=account,
        credit_native=Decimal("10"),
        base_cost_rub=Decimal("1000"),
        fees_rub=Decimal("0"),
        purchased_at=timezone.now(),
        created_by=user,
        market_fx_rate_rub=Decimal("100"),
    )
    reservation = reserve_provider_spend(
        provider=provider,
        amount_native=Decimal("5"),
        source_key="test-overrun:1",
    )

    spend = settle_provider_spend(
        reservation_id=reservation.id,
        actual_native=Decimal("7"),
        nominal_cost_rub=Decimal("700"),
        customer_charge_rub=Decimal("900"),
        source_type="test",
        source_id="overrun-1",
        model_slug="expensive-model",
        input_tokens=100,
        output_tokens=200,
    )

    account.refresh_from_db()
    provider.refresh_from_db()
    reservation.refresh_from_db()
    assert spend is not None
    assert ProviderSpend.objects.get(pk=spend.pk).native_cost == Decimal("7.000000")
    assert account.spent_native == Decimal("7.000000")
    assert account.reserved_native == Decimal("0.000000")
    assert provider.emergency_disabled is True
    assert reservation.state == reservation.State.SETTLED
    anomaly = CostAnomaly.objects.get(dedupe_key="provider-procurement-overrun:test:overrun-1")
    assert anomaly.severity == "critical"
    assert anomaly.details["actual_native"] == "7.000000"
