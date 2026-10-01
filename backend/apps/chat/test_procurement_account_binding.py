from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.billing.models import PriceVersion, RequestCost
from apps.procurement.models import ProviderFundingAccount, ProviderSpendReservation

from .procurement_execution import _rebind_failed_provider_reservation, install


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
    module.record_failure = lambda _provider, _error, adapter=None: None

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


@pytest.mark.django_db
def test_candidate_funding_race_does_not_poison_provider_or_unrelated_keys():
    provider = Provider.objects.create(
        slug="chat-funding-race",
        name="Chat funding race",
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    primary_key = _key(provider, label="funded", secret="sk-funded", priority=1)
    backup_key = _key(provider, label="healthy-backup", secret="sk-healthy-backup", priority=2)

    calls = []
    module = SimpleNamespace()
    module.provider_available = lambda _provider: True
    module._snapshot_capacity = lambda _model, _route_price: True
    module.adapter_for = lambda _model, *args, **kwargs: object()

    def raw_record_failure(selected_provider, error, adapter=None):
        calls.append((selected_provider.id, error.code, adapter))
        Provider.objects.filter(pk=selected_provider.pk).update(
            health_state=Provider.HealthState.DEGRADED
        )
        ProviderApiKey.objects.filter(pk=backup_key.pk).update(
            health_state=ProviderApiKey.HealthState.DEGRADED
        )

    module.record_failure = raw_record_failure
    module.run = lambda _generation, *args, **kwargs: iter(())
    install(module)

    module.record_failure(
        provider,
        ProviderError(
            "Reserved funding credential is no longer execution-ready",
            code="candidate_not_ready",
            retryable=False,
        ),
        adapter=None,
    )

    provider.refresh_from_db()
    primary_key.refresh_from_db()
    backup_key.refresh_from_db()
    assert calls == []
    assert provider.health_state == Provider.HealthState.HEALTHY
    assert primary_key.health_state == ProviderApiKey.HealthState.HEALTHY
    assert backup_key.health_state == ProviderApiKey.HealthState.HEALTHY


@pytest.mark.django_db(transaction=True)
@override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
def test_active_chat_reserve_rebinds_to_healthy_funded_key_without_double_reserve():
    provider = Provider.objects.create(
        slug="chat-account-rebind",
        name="Chat account rebind",
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
        enabled=True,
        health_state=Provider.HealthState.DEGRADED,
    )
    first_key = _key(provider, label="first", secret="sk-first", priority=1)
    second_key = _key(provider, label="second", secret="sk-second", priority=2)
    first = ProviderFundingAccount.objects.create(
        provider=provider,
        api_key=first_key,
        label="First",
        currency="USD",
        active=True,
        is_default=True,
        priority=1,
        funded_native=Decimal("10"),
    )
    second = ProviderFundingAccount.objects.create(
        provider=provider,
        api_key=second_key,
        label="Second",
        currency="USD",
        active=True,
        priority=2,
        funded_native=Decimal("10"),
    )
    model = AIModel.objects.create(
        provider=provider,
        slug="chat-account-rebind-model",
        display_name="Chat account rebind model",
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
    assert reservation.account_id == first.id
    first.refresh_from_db()
    second.refresh_from_db()
    reserved = reservation.amount_native
    assert first.reserved_native == reserved
    assert second.reserved_native == Decimal("0")

    ProviderApiKey.objects.filter(pk=first_key.pk).update(
        health_state=ProviderApiKey.HealthState.DEGRADED
    )
    assert _rebind_failed_provider_reservation(generation_id, provider) is True

    reservation.refresh_from_db()
    first.refresh_from_db()
    second.refresh_from_db()
    assert reservation.account_id == second.id
    assert first.reserved_native == Decimal("0")
    assert second.reserved_native == reserved
    assert ProviderSpendReservation.objects.filter(source_key=reservation.source_key).count() == 1

    # Repeating the recovery path is idempotent while the replacement stays healthy.
    assert _rebind_failed_provider_reservation(generation_id, provider) is False
    second.refresh_from_db()
    assert second.reserved_native == reserved
