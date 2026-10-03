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
def test_unbound_chat_transport_can_use_healthy_spare_when_funding_key_is_not_ready():
    provider = _provider("funding-key-contract")
    funding_key = _key(
        provider,
        "funding",
        "funding-secret",
        ProviderApiKey.HealthState.UNKNOWN,
    )
    spare = _key(
        provider,
        "healthy-spare",
        "spare-secret",
        ProviderApiKey.HealthState.HEALTHY,
    )
    account = ProviderFundingAccount.objects.create(
        provider=provider,
        api_key=funding_key,
        label="Основной закупочный аккаунт",
        currency="USD",
        active=True,
        is_default=True,
        funded_native=100,
    )

    # Strict procurement selection still refuses the not-yet-verified funded key.
    strict_secret, strict_key_id = dispatch.select_runtime_api_key(
        provider, allow_probe=False, touch=False
    )
    assert strict_secret == ""
    assert strict_key_id == funding_key.id

    # Customer transport readiness is independent until an exact provider-spend
    # reservation pins execution to a funding account.
    assert dispatch.runtime_credential_ready(provider) is True
    transport_secret, transport_key_id = dispatch.select_runtime_api_key(
        provider,
        allow_probe=False,
        touch=False,
        require_funding_balance=False,
    )
    assert transport_secret == "spare-secret"
    assert transport_key_id == spare.id

    # Once a concrete reservation/account is specified, exact-account identity is
    # still fail-closed and cannot silently switch to the spare key.
    pinned_secret, pinned_key_id = dispatch.select_runtime_api_key(
        provider,
        allow_probe=False,
        touch=False,
        funding_account_id=account.id,
        require_funding_balance=False,
    )
    assert pinned_secret == ""
    assert pinned_key_id == funding_key.id

    probe_secret, probe_key_id = dispatch.select_runtime_api_key(
        provider, allow_probe=True, touch=False
    )
    assert probe_secret == "funding-secret"
    assert probe_key_id == funding_key.id


@pytest.mark.django_db
def test_polza_selects_only_key_that_exposes_requested_model():
    provider = _provider("polza")
    provider.api_base_url = "https://polza.ai/api/v1"
    provider.save(update_fields=["api_base_url"])
    first = _key(
        provider,
        "claude-only",
        "pza-claude",
        ProviderApiKey.HealthState.HEALTHY,
    )
    first.allowed_models = ["anthropic/claude-sonnet-5.5"]
    first.save(update_fields=["allowed_models"])
    second = _key(
        provider,
        "openai-only",
        "pza-openai",
        ProviderApiKey.HealthState.HEALTHY,
    )
    second.allowed_models = ["openai/gpt-6-luna"]
    second.save(update_fields=["allowed_models"])

    secret, key_id = dispatch.select_runtime_api_key(
        provider,
        allow_probe=False,
        touch=False,
        require_funding_balance=False,
        model_upstream="openai/gpt-6-luna",
    )
    assert secret == "pza-openai"
    assert key_id == second.id

    secret, key_id = dispatch.select_runtime_api_key(
        provider,
        allow_probe=False,
        touch=False,
        require_funding_balance=False,
        model_upstream="anthropic/claude-sonnet-5.5",
    )
    assert secret == "pza-claude"
    assert key_id == first.id


@pytest.mark.django_db
def test_polza_empty_allowlist_blocks_customer_runtime_key():
    provider = _provider("polza")
    provider.api_base_url = "https://polza.ai/api/v1"
    provider.save(update_fields=["api_base_url"])
    key = _key(
        provider,
        "router-key",
        "pza-router",
        ProviderApiKey.HealthState.HEALTHY,
    )
    key.available_models = ["openai/gpt-6.1-sol"]
    key.allowed_models = []
    key.save(update_fields=["available_models", "allowed_models"])

    secret, key_id = dispatch.select_runtime_api_key(
        provider,
        allow_probe=False,
        touch=False,
        require_funding_balance=False,
        model_upstream="openai/gpt-6.1-sol",
    )
    assert secret == ""
    assert key_id is None
