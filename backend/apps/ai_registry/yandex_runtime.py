from __future__ import annotations

import os

from .yandexgpt_adapter import DEFAULT_BASE_URL, YandexGPTAdapter


def install(dispatch_module) -> None:
    raw_adapter_for = dispatch_module.adapter_for
    if getattr(raw_adapter_for, "_ai_workspace_yandexgpt", False):
        return

    def adapter_for(
        model,
        *,
        allow_probe: bool = False,
        funding_account_id=None,
        require_funding_balance: bool = True,
    ):
        provider = model.provider
        if provider.slug != "yandexgpt":
            return raw_adapter_for(
                model,
                allow_probe=allow_probe,
                funding_account_id=funding_account_id,
                require_funding_balance=require_funding_balance,
            )

        api_key, key_id = dispatch_module.select_runtime_api_key(
            provider,
            allow_probe=allow_probe,
            funding_account_id=funding_account_id,
            require_funding_balance=require_funding_balance,
        )
        if not api_key:
            from .adapters import ProviderError

            raise ProviderError(
                "YandexGPT credential is not execution-ready",
                code="candidate_not_ready",
                retryable=False,
            )
        config = provider.auth_config or {}
        folder_id = str(config.get("folder_id") or os.getenv("YANDEX_CLOUD_FOLDER_ID", "")).strip()
        adapter = YandexGPTAdapter(
            api_key=api_key,
            folder_id=folder_id,
            base_url=provider.api_base_url
            or str(config.get("base_url") or "").strip()
            or os.getenv("YANDEXGPT_API_BASE_URL", DEFAULT_BASE_URL),
            probe_model=model.upstream_model or "yandexgpt/latest",
        )
        return dispatch_module._bind_runtime_identity(
            adapter,
            key_id=key_id,
            model=model,
            allow_probe=allow_probe,
            funding_account_id=funding_account_id,
        )

    adapter_for._ai_workspace_yandexgpt = True
    adapter_for._raw_adapter_for = raw_adapter_for
    dispatch_module.adapter_for = adapter_for
