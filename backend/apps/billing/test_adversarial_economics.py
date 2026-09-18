from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.b2b_api.models import APIKey, Organization

from .models import CostAnomaly, MarginPolicyVersion, PriceVersion, RequestCost
from .reconciliation import record_cost_outcome
from .services import credit, reserve, settle


@pytest.mark.django_db(transaction=True)
def test_user_cannot_spend_more_than_prefunded_wallet_even_with_multiple_reservations():
    user = User.objects.create_user(username="solvency", password="password123")
    credit(user, Decimal("1000"), "test", "prefund")

    first = reserve(user, Decimal("999"), "attack:first")
    with pytest.raises(ValidationError):
        reserve(user, Decimal("2"), "attack:second")

    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("1.0000")
    assert user.wallet.reserved_rub == Decimal("999.0000")
    assert first.amount_rub == Decimal("999.0000")


@pytest.mark.django_db(transaction=True)
def test_settlement_can_never_charge_above_pre_authorized_reservation():
    user = User.objects.create_user(username="settlement-cap", password="password123")
    credit(user, Decimal("1000"), "test", "settlement-cap")
    reservation = reserve(user, Decimal("10"), "attack:reserve")

    with pytest.raises(ValidationError):
        settle(reservation.id, Decimal("100"))

    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("990.0000")
    assert user.wallet.reserved_rub == Decimal("10.0000")


@pytest.mark.django_db
def test_guaranteed_negative_margin_trips_provider_circuit_immediately():
    MarginPolicyVersion.objects.create(
        minimum_gross_margin_percent=25,
        anomaly_cost_deviation_percent=20,
        reconciliation_threshold_rub=1,
        effective_from=timezone.now(),
    )
    provider = Provider.objects.create(slug="loss-provider", name="Loss Provider")
    model = AIModel.objects.create(
        provider=provider,
        slug="loss-model",
        display_name="Loss Model",
        upstream_model="loss-model-v1",
    )
    price = PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("100"),
        output_rub_per_million=Decimal("100"),
        effective_from=timezone.now(),
    )
    request_cost = RequestCost.objects.create(
        generation_id=model.id,
        price_version=price,
        estimated_rub=Decimal("5"),
        expected_provider_cost_rub=Decimal("2"),
        provider_cost_rub=Decimal("8"),
        charged_rub=Decimal("5"),
        gross_profit_rub=Decimal("-3"),
        gross_margin_percent=Decimal("-60"),
    )

    record_cost_outcome(request_cost, model=model)

    provider.refresh_from_db()
    assert provider.emergency_disabled is True
    anomaly = CostAnomaly.objects.get(dedupe_key=f"critical-loss:{request_cost.id}")
    assert anomaly.severity == "critical"
    assert anomaly.provider_slug == provider.slug


@pytest.mark.django_db
def test_economic_gate_blocks_unbounded_b2b_key():
    user = User.objects.create_user(username="b2b-owner", password="password123")
    org = Organization.objects.create(name="Unsafe", slug="unsafe", billing_user=user)
    APIKey.objects.create(
        organization=org,
        created_by=user,
        name="unbounded",
        prefix="ak_unbounded",
        secret_hash="0" * 64,
        scopes=["chat.completions"],
        monthly_limit_rub=None,
    )

    with pytest.raises(CommandError, match="b2b_keys_without_any_spend_limit"):
        call_command("economic_safety_check")
