from datetime import timedelta

import pytest
from django.utils import timezone

from .models import Provider, ProviderApiKey
from .reliability import provider_available


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
