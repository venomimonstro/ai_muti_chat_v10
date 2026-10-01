import pytest

from apps.procurement.models import ProviderFundingAccount

from . import adapters, dispatch, reliability
from .gigachat_adapter import GigaChatAPIAdapter
from .models import AIModel, Provider, ProviderApiKey


def _provider(slug: str):
    return Provider.objects.create(
        slug=slug,
        name=slug,
        enabled=True,
        adapter_type=Provider.AdapterType.ECHO,
    )


def _key(provider, label, secret, health):
    key = ProviderApiKey(provider=provider, label=label, health_state=health)
    key.set_secret(secret)
    key.save()
    return key


@pytest.mark.django_db
def test_reliability_uses_fail_safe_dispatcher_after_app_startup():
    assert adapters.adapter_for is dispatch.adapter_for
    assert reliability.adapter_for is dispatch.adapter_for


@pytest.mark.django_db
def test_legacy_gigachat_echo_row_never_dispatches_to_echo():
    provider = _provider("gigachat")
    key = ProviderApiKey(provider=provider, label="test")
    key.set_secret("credential")
    key.save()
    model = AIModel.objects.create(
        provider=provider,
        slug="gigachat-runtime-test",
        display_name="System Pro",
        upstream_model="GigaChat-2-Pro",
        enabled=True,
    )

    adapter = dispatch.adapter_for(model)
    assert isinstance(adapter, GigaChatAPIAdapter)


@pytest.mark.django_db
def test_customer_dispatch_never_uses_unknown_or_degraded_pool_key():
    provider = _provider("strict-customer-keys")
    unknown = _key(
        provider,
        "unknown",
        "unknown-secret",
        ProviderApiKey.HealthState.UNKNOWN,
    )
    degraded = _key(
        provider,
        "degraded",
        "degraded-secret",
        ProviderApiKey.HealthState.DEGRADED,
    )
    healthy = _key(
        provider,
        "healthy",
        "healthy-secret",
        ProviderApiKey.HealthState.HEALTHY,
    )

    secret, key_id = dispatch.select_runtime_api_key(provider, allow_probe=False, touch=False)
    assert secret == "healthy-secret"
    assert key_id == healthy.id
    assert key_id not in {unknown.id, degraded.id}


@pytest.mark.django_db
def test_default_funding_account_blocks_customer_use_until_its_key_is_healthy():
    provider = _provider("funding-key-contract")
    funding_key = _key(
        provider,
        "funding",
        "funding-secret",
        ProviderApiKey.HealthState.UNKNOWN,
    )
    _key(
        provider,
        "healthy-spare",
        "spare-secret",
        ProviderApiKey.HealthState.HEALTHY,
    )
    ProviderFundingAccount.objects.create(
        provider=provider,
        api_key=funding_key,
        label="Основной закупочный аккаунт",
        currency="USD",
        active=True,
        is_default=True,
        funded_native=100,
    )

    secret, key_id = dispatch.select_runtime_api_key(provider, allow_probe=False, touch=False)
    assert secret == ""
    assert key_id == funding_key.id
    assert dispatch.runtime_credential_ready(provider) is False

    probe_secret, probe_key_id = dispatch.select_runtime_api_key(
        provider, allow_probe=True, touch=False
    )
    assert probe_secret == "funding-secret"
    assert probe_key_id == funding_key.id
