from __future__ import annotations

from apps.ai_registry.models import ProviderApiKey


def install(paid_search_module) -> None:
    """Keep customer paid-search traffic off unverified/degraded API keys.

    Health/recovery tooling may probe UNKNOWN or DEGRADED credentials separately.
    Normal chat search must be fail-closed and use only a key that has already been
    verified HEALTHY. Funding accounts backed only by environment credentials keep
    their existing behavior because they have no ProviderApiKey health row.
    """
    raw_account_secret = paid_search_module._account_secret
    if getattr(raw_account_secret, "_ai_workspace_paid_search_readiness", False) is True:
        return

    def account_secret(account) -> str:
        if account.api_key_id:
            key = account.api_key
            if (
                not key.enabled
                or key.health_state != ProviderApiKey.HealthState.HEALTHY
            ):
                return ""
        return raw_account_secret(account)

    account_secret._ai_workspace_paid_search_readiness = True
    account_secret._raw_account_secret = raw_account_secret
    paid_search_module._account_secret = account_secret
