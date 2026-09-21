from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.models import Provider, ProviderApiKey
from apps.procurement.models import ProviderSpendAllocation
from apps.procurement.services import (
    create_funding_account,
    record_purchase,
    reserve_provider_spend,
    settle_provider_spend,
)


@pytest.mark.django_db(transaction=True)
def test_key_linked_account_drives_runtime_key_and_fifo_purchase_cost():
    user = User.objects.create_user(username="owner-ledger", email="owner-ledger@example.test", password="password123")
    provider = Provider.objects.create(
        slug="ledger-vendor",
        name="Ledger vendor",
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
    )
    other_key = ProviderApiKey(provider=provider, label="Резервный", enabled=True, priority=1, health_state="healthy")
    other_key.set_secret("reserve-secret")
    other_key.save()
    paid_key = ProviderApiKey(provider=provider, label="Купленный ключ", enabled=True, priority=100, health_state="healthy")
    paid_key.set_secret("paid-secret")
    paid_key.save()

    account = create_funding_account(
        provider=provider,
        api_key=paid_key,
        label="Купленный ключ",
        currency="USD",
        is_default=True,
    )
    assert provider.get_api_key() == "paid-secret"

    first = record_purchase(
        account=account,
        credit_native=Decimal("10"),
        payment_amount=Decimal("10"),
        payment_currency="USD",
        payment_fx_rate_rub=Decimal("100"),
        fees_rub=Decimal("0"),
        purchased_at=timezone.now(),
        created_by=user,
    )
    second = record_purchase(
        account=account,
        credit_native=Decimal("10"),
        payment_amount=Decimal("20"),
        payment_currency="USD",
        payment_fx_rate_rub=Decimal("100"),
        fees_rub=Decimal("0"),
        purchased_at=timezone.now(),
        created_by=user,
    )
    assert first.document_number.startswith("API-")
    assert first.total_cash_outlay_rub == Decimal("1000.0000")
    assert second.total_cash_outlay_rub == Decimal("2000.0000")

    reservation = reserve_provider_spend(provider=provider, amount_native=Decimal("12"), source_key="fifo:test")
    spend = settle_provider_spend(
        reservation_id=reservation.id,
        actual_native=Decimal("12"),
        nominal_cost_rub=Decimal("1200"),
        customer_charge_rub=Decimal("2400"),
        source_type="chat",
        source_id="fifo-test",
        model_slug="model-a",
    )
    allocations = list(ProviderSpendAllocation.objects.filter(spend=spend).order_by("purchase__purchased_at", "created_at"))
    assert len(allocations) == 2
    assert allocations[0].purchase_id == first.id
    assert allocations[0].native_amount == Decimal("10.000000")
    assert allocations[0].economic_cost_rub == Decimal("1000.0000")
    assert allocations[1].purchase_id == second.id
    assert allocations[1].native_amount == Decimal("2.000000")
    assert allocations[1].economic_cost_rub == Decimal("400.0000")
    assert spend.economic_cost_rub == Decimal("1400.0000")
    assert spend.customer_charge_rub == Decimal("2400.0000")
