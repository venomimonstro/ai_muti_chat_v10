from decimal import Decimal

import pytest

from apps.ai_registry import dispatch
from apps.ai_registry.models import Provider, ProviderApiKey

from .account_routing import (
    reserve_provider_spend,
    select_runtime_funding_account,
)
from .models import ProviderFundingAccount


def _key(provider, label, secret, health, priority):
    key = ProviderApiKey(
        provider=provider,
        label=label,
        enabled=True,
        health_state=health,
        priority=priority,
    )
    key.set_secret(secret)
    key.save()
    return key


def _account(provider, key, *, label, funded, default=False, priority=100, currency="USD"):
    return ProviderFundingAccount.objects.create(
        provider=provider,
        api_key=key,
        label=label,
        currency=currency,
        active=True,
        is_default=default,
        priority=priority,
        funded_native=Decimal(str(funded)),
    )


@pytest.mark.django_db(transaction=True)
def test_depleted_default_account_fails_over_to_healthy_paid_backup():
    provider = Provider.objects.create(
        slug="multi-paid",
        name="Multi paid",
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    primary_key = _key(
        provider,
        "primary",
        "sk-primary",
        ProviderApiKey.HealthState.HEALTHY,
        1,
    )
    backup_key = _key(
        provider,
        "backup",
        "sk-backup",
        ProviderApiKey.HealthState.HEALTHY,
        10,
    )
    primary = _account(
        provider,
        primary_key,
        label="Primary",
        funded="0",
        default=True,
        priority=1,
    )
    backup = _account(
        provider,
        backup_key,
        label="Backup",
        funded="10",
        priority=10,
    )

    selected = select_runtime_funding_account(
        provider,
        required_native=Decimal("2"),
        currency="USD",
    )
    assert selected.pk == backup.pk

    reservation = reserve_provider_spend(
        provider=provider,
        amount_native=Decimal("2"),
        source_key="test:paid-account-failover",
        currency="USD",
    )
    reservation.refresh_from_db()
    primary.refresh_from_db()
    backup.refresh_from_db()

    assert reservation.account_id == backup.id
    assert primary.reserved_native == Decimal("0")
    assert backup.reserved_native == Decimal("2")
    secret, key_id = dispatch.select_runtime_api_key(
        provider,
        funding_account_id=backup.id,
        touch=False,
    )
    assert secret == "sk-backup"
    assert key_id == backup_key.id


@pytest.mark.django_db(transaction=True)
def test_degraded_default_key_does_not_hide_healthy_backup_account():
    provider = Provider.objects.create(
        slug="multi-health",
        name="Multi health",
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
        enabled=True,
        health_state=Provider.HealthState.DEGRADED,
    )
    bad_key = _key(
        provider,
        "bad-default",
        "sk-bad",
        ProviderApiKey.HealthState.DEGRADED,
        1,
    )
    good_key = _key(
        provider,
        "healthy-backup",
        "sk-good",
        ProviderApiKey.HealthState.HEALTHY,
        100,
    )
    _account(
        provider,
        bad_key,
        label="Bad default",
        funded="10",
        default=True,
        priority=1,
    )
    good = _account(
        provider,
        good_key,
        label="Good backup",
        funded="10",
        priority=100,
    )

    selected = select_runtime_funding_account(
        provider,
        required_native=Decimal("1"),
        currency="USD",
    )
    assert selected.pk == good.pk
    secret, key_id = dispatch.select_runtime_api_key(provider, touch=False)
    assert secret == "sk-good"
    assert key_id == good_key.id
    assert dispatch.runtime_credential_ready(provider) is True


@pytest.mark.django_db(transaction=True)
def test_quote_currency_never_reserves_different_currency_account():
    provider = Provider.objects.create(
        slug="multi-currency",
        name="Multi currency",
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    usd_key = _key(
        provider,
        "usd",
        "sk-usd",
        ProviderApiKey.HealthState.HEALTHY,
        1,
    )
    eur_key = _key(
        provider,
        "eur",
        "sk-eur",
        ProviderApiKey.HealthState.HEALTHY,
        2,
    )
    usd = _account(
        provider,
        usd_key,
        label="USD",
        funded="100",
        default=True,
        priority=1,
        currency="USD",
    )
    eur = _account(
        provider,
        eur_key,
        label="EUR",
        funded="5",
        priority=2,
        currency="EUR",
    )

    reservation = reserve_provider_spend(
        provider=provider,
        amount_native=Decimal("3"),
        source_key="test:currency-safe",
        currency="EUR",
    )
    usd.refresh_from_db()
    eur.refresh_from_db()

    assert reservation.account_id == eur.id
    assert usd.reserved_native == Decimal("0")
    assert eur.reserved_native == Decimal("3")
