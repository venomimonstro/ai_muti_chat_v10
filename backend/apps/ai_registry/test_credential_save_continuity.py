import pytest

from .models import Provider, ProviderApiKey


@pytest.fixture
def provider(db, monkeypatch):
    monkeypatch.setattr("apps.ai_registry.signals._schedule_health_probe", lambda: None)
    return Provider.objects.create(slug="credential-continuity", name="Continuity", adapter_type=Provider.AdapterType.OPENAI_RESPONSES, health_state=Provider.HealthState.HEALTHY)


def key(provider, label, state):
    item = ProviderApiKey(provider=provider, label=label, health_state=state)
    item.set_secret("test-secret")
    item.save()
    return item


def test_healthy_key_metadata_save_does_not_take_provider_offline(provider):
    item = key(provider, "healthy", ProviderApiKey.HealthState.HEALTHY)
    item.priority = 2
    item.save(update_fields=["priority"])
    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.HEALTHY


def test_unknown_new_key_keeps_verified_sibling_channel_available(provider):
    verified = key(provider, "verified", ProviderApiKey.HealthState.HEALTHY)
    added = key(provider, "new", ProviderApiKey.HealthState.UNKNOWN)
    provider.refresh_from_db()
    verified.refresh_from_db()
    assert provider.health_state == Provider.HealthState.HEALTHY
    assert verified.health_state == ProviderApiKey.HealthState.HEALTHY
    assert added.health_state == ProviderApiKey.HealthState.UNKNOWN


def test_replacing_secret_requires_reverification_even_with_partial_save(provider):
    item = key(provider, "healthy", ProviderApiKey.HealthState.HEALTHY)
    item.set_secret("replacement-secret")
    item.save(update_fields=["secret_encrypted"])
    item.refresh_from_db()
    provider.refresh_from_db()
    assert item.health_state == ProviderApiKey.HealthState.UNKNOWN
    assert provider.health_state == Provider.HealthState.UNKNOWN
