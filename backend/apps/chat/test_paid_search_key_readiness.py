from types import SimpleNamespace

from apps.ai_registry.models import ProviderApiKey

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
