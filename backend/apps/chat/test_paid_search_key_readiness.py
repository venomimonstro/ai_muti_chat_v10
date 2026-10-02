from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.accounts.models import User
from apps.ai_registry.models import Provider, ProviderApiKey
from apps.procurement.services import create_funding_account, record_purchase

from .paid_search_readiness import install


def _module():
    def account_secret(account):
        return str(getattr(account, "secret", "") or "")

    return SimpleNamespace(_account_secret=account_secret)


def _account(state, *, enabled=True, secret="secret"):
    key = SimpleNamespace(enabled=enabled, health_state=state)
    return SimpleNamespace(api_key_id="key-id", api_key=key, secret=secret)


def test_paid_search_customer_path_requires_healthy_key():
    module = _module()
    install(module)

    assert module._account_secret(_account(ProviderApiKey.HealthState.HEALTHY)) == "secret"
    assert module._account_secret(_account(ProviderApiKey.HealthState.UNKNOWN)) == ""
    assert module._account_secret(_account(ProviderApiKey.HealthState.DEGRADED)) == ""
    assert module._account_secret(_account(ProviderApiKey.HealthState.DISABLED)) == ""
    assert module._account_secret(_account(ProviderApiKey.HealthState.HEALTHY, enabled=False)) == ""


def test_paid_search_env_credential_keeps_existing_non_key_path():
    module = _module()
    install(module)
    account = SimpleNamespace(api_key_id=None, secret="env-secret")

    assert module._account_secret(account) == "env-secret"


@pytest.mark.django_db(transaction=True)
def test_paid_search_uses_healthy_funded_backup_when_default_key_is_degraded():
    from apps.chat.paid_search_billing import _provider_and_account

    owner = User.objects.create_user(
        username="paid-search-backup-owner",
        email="paid-search-backup-owner@example.test",
        password="password123",
    )
    provider = Provider.objects.create(
        slug="yandex-search",
        name="Yandex Search",
        enabled=True,
        emergency_disabled=False,
    )

    degraded = ProviderApiKey(
        provider=provider,
        label="degraded-default",
        enabled=True,
        health_state=ProviderApiKey.HealthState.DEGRADED,
    )
    degraded.set_secret("search-degraded-secret")
    degraded.save()
    healthy = ProviderApiKey(
        provider=provider,
        label="healthy-backup",
        enabled=True,
        health_state=ProviderApiKey.HealthState.HEALTHY,
    )
    healthy.set_secret("search-healthy-secret")
    healthy.save()

    default_account = create_funding_account(
        provider=provider,
        api_key=degraded,
        label="Degraded default",
        currency="RUB",
        priority=1,
        is_default=True,
    )
    backup_account = create_funding_account(
        provider=provider,
        api_key=healthy,
        label="Healthy backup",
        currency="RUB",
        priority=10,
        is_default=False,
    )
    record_purchase(
        account=default_account,
        credit_native=Decimal("10"),
        base_cost_rub=Decimal("10"),
        created_by=owner,
    )
    record_purchase(
        account=backup_account,
        credit_native=Decimal("10"),
        base_cost_rub=Decimal("10"),
        created_by=owner,
    )

    selected_provider, selected_account, unit_cost = _provider_and_account()

    assert selected_provider.id == provider.id
    assert selected_account.id == backup_account.id
    assert selected_account.api_key_id == healthy.id
    assert unit_cost > Decimal("0")
