from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.billing.models import PriceVersion, RequestCost
from apps.procurement.models import ProviderFundingAccount, ProviderSpendReservation

from .procurement_execution import install


def _key(provider, *, label, secret, priority=100):
    key = ProviderApiKey(
        provider=provider,
        label=label,
        enabled=True,
        health_state=ProviderApiKey.HealthState.HEALTHY,
        priority=priority,
    )
    key.set_secret(secret)
    key.save()
    return key


@pytest.mark.django_db(transaction=True)
@override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
def test_chat_provider_call_is_pinned_to_account_that_owns_procurement_reservation():
    provider = Provider.objects.create(
        slug="chat-account-binding",
        name="Chat account binding",
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    primary_key = _key(provider, label="primary", secret="sk-primary", priority=1)
    backup_key = _key(provider, label="backup", secret="sk-backup", priority=2)
    primary = ProviderFundingAccount.objects.create(
        provider=provider,
        api_key=primary_key,
        label="Primary",
        currency="USD",
        active=True,
        is_default=True,
        priority=1,
        funded_native=Decimal("0"),
    )
    backup = ProviderFundingAccount.objects.create(
        provider=provider,
        api_key=backup_key,
        label="Backup",
        currency="USD",
        active=True,
        priority=2,
        funded_native=Decimal("5"),
    )
    Provider.objects.filter(pk=provider.pk).update(health_state=Provider.HealthState.HEALTHY)
    provider.refresh_from_db()

    model = AIModel.objects.create(
        provider=provider,
        slug="chat-account-binding-model",
        display_name="Chat account binding model",
        upstream_model="upstream-model",
        enabled=True,
    )
    price = PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("1"),
        output_rub_per_million=Decimal("2"),
        provider_currency="USD",
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("2"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    generation_id = uuid4()
    cost = RequestCost.objects.create(
        generation_id=generation_id,
        price_version=price,
        estimated_rub=Decimal("4.0000"),
        expected_provider_cost_rub=Decimal("2.0000"),
        pricing_snapshot={"fx_rate": "1", "provider_currency": "USD"},
    )
    reservation = ProviderSpendReservation.objects.get(
        source_key=f"chat:{cost.id}:{price.id}",
        state=ProviderSpendReservation.State.ACTIVE,
    )
    assert reservation.account_id == backup.id
    primary.refresh_from_db()
    assert primary.reserved_native == Decimal("0")

    observed = {}
    module = SimpleNamespace()
    module.provider_available = lambda _provider: True
    module._snapshot_capacity = lambda _model, _route_price: True

    def raw_adapter_for(selected, *args, **kwargs):
        observed["model"] = selected.slug
        observed["funding_account_id"] = kwargs.get("funding_account_id")
        return object()

    module.adapter_for = raw_adapter_for

    def raw_run(generation, *args, **kwargs):
        module.adapter_for(model)
        yield "ok"

    module.run = raw_run
    install(module)

    assert list(module.run(SimpleNamespace(id=generation_id))) == ["ok"]
    assert observed["model"] == model.slug
    assert str(observed["funding_account_id"]) == str(backup.id)
