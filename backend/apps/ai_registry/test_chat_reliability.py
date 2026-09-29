from datetime import timedelta

import pytest
from django.utils import timezone

from .adapters import ProviderError
from .models import Provider, ProviderApiKey
from .reliability import provider_available, record_failure, record_success


def _provider(**overrides):
    values = {
        "slug": "chat-reliability-provider",
        "name": "Chat Reliability Provider",
        "enabled": True,
        "health_state": Provider.HealthState.HEALTHY,
    }
    values.update(overrides)
    return Provider.objects.create(**values)


def _key(provider, label, secret, *, state=ProviderApiKey.HealthState.HEALTHY, priority=100):
    key = ProviderApiKey(
        provider=provider,
        label=label,
        enabled=True,
        priority=priority,
        health_state=state,
        secret_encrypted="",
    )
    key.set_secret(secret)
    key.save()
    return key


@pytest.mark.django_db
def test_healthy_key_does_not_bypass_active_provider_circuit():
    provider = _provider(
        health_state=Provider.HealthState.OPEN,
        consecutive_failures=3,
        circuit_opened_until=timezone.now() + timedelta(minutes=5),
    )
    _key(provider, "healthy", "secret-one")

    assert provider_available(provider) is False

    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.OPEN
    assert provider.consecutive_failures == 3
    assert provider.circuit_opened_until is not None


@pytest.mark.django_db
def test_elapsed_circuit_allows_probe_without_erasing_failure_history(monkeypatch):
    monkeypatch.setenv("CHAT_RELIABILITY_PROVIDER_KEY", "legacy-probe-key")
    provider = _provider(
        health_state=Provider.HealthState.OPEN,
        consecutive_failures=4,
        circuit_opened_until=timezone.now() - timedelta(seconds=1),
        credential_env="CHAT_RELIABILITY_PROVIDER_KEY",
    )

    assert provider_available(provider) is True

    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.OPEN
    assert provider.consecutive_failures == 4


@pytest.mark.django_db
def test_key_pool_rotates_away_from_just_used_healthy_key():
    provider = _provider()
    first = _key(provider, "first", "secret-first", priority=10)
    second = _key(provider, "second", "secret-second", priority=20)

    secret_one, key_one = provider.select_api_key()
    secret_two, key_two = provider.select_api_key()

    assert {secret_one, secret_two} == {"secret-first", "secret-second"}
    assert {key_one, key_two} == {first.id, second.id}
    assert key_one != key_two


@pytest.mark.django_db
def test_degraded_key_is_used_only_after_healthier_keys():
    provider = _provider()
    degraded = _key(
        provider,
        "degraded",
        "degraded-secret",
        state=ProviderApiKey.HealthState.DEGRADED,
        priority=1,
    )
    healthy = _key(
        provider,
        "healthy",
        "healthy-secret",
        state=ProviderApiKey.HealthState.HEALTHY,
        priority=100,
    )

    secret, key_id = provider.select_api_key()

    assert secret == "healthy-secret"
    assert key_id == healthy.id
    assert key_id != degraded.id


@pytest.mark.django_db
def test_non_retryable_bad_key_becomes_retryable_when_spare_key_exists():
    provider = _provider()
    first = _key(provider, "first", "secret-first", priority=10)
    second = _key(provider, "second", "secret-second", priority=20)

    secret, selected_id = provider.select_api_key()
    assert secret in {"secret-first", "secret-second"}

    error = ProviderError("bad credential", code="authentication_error", retryable=False)
    record_failure(provider, error)

    assert error.retryable is True
    failed = ProviderApiKey.objects.get(pk=selected_id)
    assert failed.health_state == ProviderApiKey.HealthState.DEGRADED
    assert failed.last_error_code == "authentication_error"

    next_secret, next_id = provider.select_api_key()
    assert next_id in {first.id, second.id}
    assert next_id != selected_id
    assert next_secret != secret

    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.DEGRADED
    assert provider.circuit_opened_until is None


@pytest.mark.django_db
def test_credit_exhaustion_persistently_blocks_provider_without_spare_key(monkeypatch):
    monkeypatch.setenv("CHAT_RELIABILITY_PROVIDER_KEY", "legacy-credit-key")
    provider = _provider(credential_env="CHAT_RELIABILITY_PROVIDER_KEY")

    error = ProviderError(
        "no credits",
        code="credit_balance_exhausted",
        retryable=False,
    )
    record_failure(provider, error)

    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.OPEN
    assert provider.circuit_opened_until is None
    assert provider_available(provider) is False


@pytest.mark.django_db
def test_success_restores_key_and_provider_health():
    provider = _provider(health_state=Provider.HealthState.DEGRADED, consecutive_failures=2)
    key = _key(
        provider,
        "recovered",
        "recovered-secret",
        state=ProviderApiKey.HealthState.UNKNOWN,
    )
    provider.select_api_key()

    record_success(provider, 123)

    key.refresh_from_db()
    provider.refresh_from_db()
    assert key.health_state == ProviderApiKey.HealthState.HEALTHY
    assert key.last_error_code == ""
    assert key.last_latency_ms == 123
    assert provider.health_state == Provider.HealthState.HEALTHY
    assert provider.consecutive_failures == 0
