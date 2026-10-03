"""Final customer-catalog readiness guard.

Provider readiness proves a credential/account exists and has a positive balance.
That is necessary but not sufficient for the model picker: an account can contain a
microscopic remainder that cannot fund even the smallest inference. Customer-facing
catalogs must not advertise such a model as "ready".
"""

import os
import sys


def provider_model_config_ready(model) -> bool:
    """Validate provider-specific execution metadata before customer exposure."""
    if model.provider.slug == "polza":
        upstream = str(model.upstream_model or "").strip()
        if not upstream:
            return False
        try:
            keys = model.provider.api_keys.filter(
                enabled=True,
                health_state="healthy",
            )
            for key in keys:
                allowed = list(getattr(key, "allowed_models", None) or [])
                if upstream in allowed:
                    return True
            return False
        except Exception:
            return False
    if model.provider.slug != "yandexgpt":
        return True
    upstream = str(model.upstream_model or "").strip()
    if upstream.startswith("gpt://"):
        return True
    config = model.provider.auth_config or {}
    folder_id = str(config.get("folder_id") or os.getenv("YANDEX_CLOUD_FOLDER_ID", "")).strip()
    return bool(folder_id and upstream)


def minimum_inference_fundable(model) -> bool:
    try:
        from apps.billing.pricing import active_price, quote, require_margin
        from apps.procurement.readiness import quote_has_procurement_capacity

        price = active_price(model.slug)
        value = require_margin(
            quote(
                price,
                32,
                8,
                provider_slug=model.provider.slug,
                model_slug=model.slug,
            )
        )
        return quote_has_procurement_capacity(model.provider, value)
    except Exception:
        return False


def install(reliability_module) -> None:
    raw_ready = reliability_module.model_client_ready
    if getattr(raw_ready, "_ai_workspace_minimum_funding", False) is True:
        return

    def model_client_ready(model):
        return bool(
            raw_ready(model)
            and provider_model_config_ready(model)
            and minimum_inference_fundable(model)
        )

    model_client_ready._ai_workspace_minimum_funding = True
    model_client_ready._raw_model_client_ready = raw_ready
    reliability_module.model_client_ready = model_client_ready

    for module_name in (
        "apps.ai_registry.views",
        "apps.ai_registry.serializers",
        "apps.chat.serializers",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "model_client_ready"):
            module.model_client_ready = model_client_ready
