from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.billing.models import PriceVersion, RequestCost
from apps.procurement.models import ProviderFundingAccount, ProviderSpendReservation
from apps.procurement.services import account_available_native

from .procurement_execution import install


def _external_provider_with_funding(*, slug, funded_native):
    provider = Provider.objects.create(
        slug=slug,
        name=slug,
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    key = ProviderApiKey(provider=provider, label="primary", enabled=True)
    key.set_secret(f"sk-{slug}")
    key.health_state = ProviderApiKey.HealthState.HEALTHY
    key.save()
    Provider.objects.filter(pk=provider.pk).update(health_state=Provider.HealthState.HEALTHY)
    provider.refresh_from_db()
    account = ProviderFundingAccount.objects.create(
        provider=provider,
        api_key=key,
        label="Primary funding",
        currency="USD",
        active=True,
        is_default=True,
        funded_native=Decimal(str(funded_native)),
    )
    return provider, account


def _model_and_price(provider, *, slug):
    model = AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug,
        upstream_model="provider-model-id",
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
    return model, price


@pytest.mark.django_db(transaction=True)
@override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
def test_execution_uses_only_its_own_provider_reservation_when_free_balance_is_zero():
    provider, account = _external_provider_with_funding(
        slug="reservation-aware-provider",
        funded_native="1.000000",
    )
    model, price = _model_and_price(provider, slug="reservation-aware-model")
    generation_id = uuid4()
    request_cost = RequestCost.objects.create(
        generation_id=generation_id,
        price_version=price,
        estimated_rub=Decimal("2.0000"),
        expected_provider_cost_rub=Decimal("1.0000"),
        pricing_snapshot={"fx_rate": "1"},
    )
    account.refresh_from_db()
    assert account_available_native(account) == Decimal("0.000000")
    source_key = f"chat:{request_cost.id}:{price.id}"
    assert ProviderSpendReservation.objects.filter(
        source_key=source_key,
        state=ProviderSpendReservation.State.ACTIVE,
    ).exists()

    observed = {}
    module = SimpleNamespace()
    module.provider_available = lambda _provider: False
    module._snapshot_capacity = lambda _model, _route_price: False

    def raw_run(generation, *args, **kwargs):
        observed["provider_available"] = module.provider_available(provider)
        observed["snapshot_capacity"] = module._snapshot_capacity(
            model,
            {"price_version_id": str(price.id)},
        )
        yield "ok"

    module.run = raw_run
    install(module)
    assert list(module.run(SimpleNamespace(id=generation_id))) == ["ok"]
    assert observed == {"provider_available": True, "snapshot_capacity": True}


@pytest.mark.django_db(transaction=True)
@override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
def test_same_provider_fallback_can_replace_primary_reservation_before_provider_call():
    provider, account = _external_provider_with_funding(
        slug="same-provider-fallback",
        funded_native="1.000000",
    )
    primary, primary_price = _model_and_price(provider, slug="same-provider-primary")
    fallback, fallback_price = _model_and_price(provider, slug="same-provider-fallback-model")
    generation_id = uuid4()
    request_cost = RequestCost.objects.create(
        generation_id=generation_id,
        price_version=primary_price,
        estimated_rub=Decimal("2.0000"),
        expected_provider_cost_rub=Decimal("1.0000"),
        pricing_snapshot={"fx_rate": "1"},
    )
    account.refresh_from_db()
    assert account_available_native(account) == Decimal("0.000000")
    primary_key = f"chat:{request_cost.id}:{primary_price.id}"
    assert ProviderSpendReservation.objects.filter(
        source_key=primary_key,
        state=ProviderSpendReservation.State.ACTIVE,
    ).exists()

    observed = {}
    module = SimpleNamespace()
    module.provider_available = lambda _provider: False
    module._snapshot_capacity = lambda _model, _route_price: False

    def raw_run(generation, *args, **kwargs):
        observed["fallback_capacity"] = module._snapshot_capacity(
            fallback,
            {"price_version_id": str(fallback_price.id)},
        )
        yield "ok"

    module.run = raw_run
    install(module)
    assert list(module.run(SimpleNamespace(id=generation_id))) == ["ok"]
    assert observed["fallback_capacity"] is True

    # The authoritative switch happens transactionally before adapter_for(). The
    # procurement signal releases the primary reserve and replaces it with the new
    # model reserve; if this reservation could not be funded the save would raise and
    # the streaming loop would skip the candidate without calling the provider.
    request_cost.price_version = fallback_price
    request_cost.expected_provider_cost_rub = Decimal("0.8000")
    request_cost.save(update_fields=["price_version", "expected_provider_cost_rub"])

    old = ProviderSpendReservation.objects.get(source_key=primary_key)
    replacement = ProviderSpendReservation.objects.get(
        source_key=f"chat:{request_cost.id}:{fallback_price.id}"
    )
    account.refresh_from_db()
    assert old.state == ProviderSpendReservation.State.RELEASED
    assert replacement.state == ProviderSpendReservation.State.ACTIVE
    assert replacement.amount_native == Decimal("0.800000")
    assert account_available_native(account) == Decimal("0.200000")
    assert primary.provider_id == fallback.provider_id


@pytest.mark.django_db(transaction=True)
@override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
def test_execution_never_borrows_another_generation_provider_reservation():
    provider, account = _external_provider_with_funding(
        slug="reservation-isolation-provider",
        funded_native="1.000000",
    )
    model, price = _model_and_price(provider, slug="reservation-isolation-model")
    first_generation = uuid4()
    first_cost = RequestCost.objects.create(
        generation_id=first_generation,
        price_version=price,
        estimated_rub=Decimal("2.0000"),
        expected_provider_cost_rub=Decimal("1.0000"),
        pricing_snapshot={"fx_rate": "1"},
    )
    account.refresh_from_db()
    assert account_available_native(account) == Decimal("0.000000")
    assert ProviderSpendReservation.objects.filter(
        source_key=f"chat:{first_cost.id}:{price.id}",
        state=ProviderSpendReservation.State.ACTIVE,
    ).exists()

    second_generation = uuid4()
    observed = {}
    module = SimpleNamespace()
    module.provider_available = lambda _provider: False
    module._snapshot_capacity = lambda _model, _route_price: False

    def raw_run(generation, *args, **kwargs):
        observed["provider_available"] = module.provider_available(provider)
        observed["snapshot_capacity"] = module._snapshot_capacity(
            model,
            {"price_version_id": str(price.id)},
        )
        yield "ok"

    module.run = raw_run
    install(module)
    assert list(module.run(SimpleNamespace(id=second_generation))) == ["ok"]
    assert observed == {"provider_available": False, "snapshot_capacity": False}
