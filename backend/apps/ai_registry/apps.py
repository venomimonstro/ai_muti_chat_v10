from django.apps import AppConfig
from django.core.checks import Error, register


@register()
def ai_registry_customer_readiness_wiring_check(app_configs, **kwargs):
    """Customer catalog/manual-selection surfaces must share one final predicate."""
    from apps.chat import serializers as chat_serializers
    from . import reliability, serializers, views

    final_ready = reliability.model_client_ready
    errors = []
    if getattr(final_ready, "_ai_workspace_minimum_funding", False) is not True:
        errors.append(
            Error(
                "AI customer readiness is missing the minimum-funding guard.",
                hint="Keep client_readiness.install(reliability) in AIRegistryConfig.ready().",
                id="ai_registry.E001",
            )
        )
    stale = []
    if views.model_client_ready is not final_ready:
        stale.append("views")
    if serializers.model_client_ready is not final_ready:
        stale.append("serializers")
    if chat_serializers.model_client_ready is not final_ready:
        stale.append("chat.serializers")
    if stale:
        errors.append(
            Error(
                "AI customer readiness surfaces retain stale predicates: "
                + ", ".join(stale),
                hint="Rebind every customer-facing model_client_ready import after final readiness installation.",
                id="ai_registry.E002",
            )
        )
    return errors




class AIRegistryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ai_registry"

    def ready(self):
        # Install fail-safe runtime dispatch and authoritative routing resolvers
        # once per process. Modules that imported adapter_for directly before
        # AppConfig.ready() must also be rebound explicitly.
        from . import (  # noqa: F401
            adapters,
            auto_continuity,
            client_readiness,
            dispatch,
            http_errors,
            manual_continuity,
            model_quarantine,
            provider_recovery,
            reliability,
            router,
            router_guardrails,
            router_intelligence,
            routing_pools,
            search_intelligence,
            signals,
            web_fetch_security,
            web_tools,
            yandex_runtime,
        )

        # YandexGPT is a real external commercial provider even if an old/manual
        # database row still carries the historical Echo default. It must never
        # inherit the test-provider readiness bypass.
        reliability.SPECIAL_EXTERNAL_PROVIDER_SLUGS.add("yandexgpt")

        http_errors.install(adapters)
        # Provider-specific dispatch is installed before quarantine/readiness so
        # YandexGPT inherits the same fail-closed health/fallback semantics.
        yandex_runtime.install(dispatch)
        adapters.adapter_for = dispatch.adapter_for
        reliability.adapter_for = dispatch.adapter_for
        search_intelligence.install(web_tools)
        # Search-provider snippets stay enabled, but arbitrary result-page fetching
        # is fail-closed unless deployment explicitly provides a safe egress boundary.
        web_fetch_security.install(web_tools)
        routing_pools.install_router_pool_resolver(router)
        # Router v3 keeps reasoning complexity independent from web/tool usage.
        # v3.3 guardrails then cover semantic edge cases that are easy to
        # underestimate with taxonomy keywords alone. Continuity is installed
        # afterwards so fallback uses the same preferred tier as the primary AUTO
        # decision.
        router_intelligence.install(router)
        router_guardrails.install(router)
        model_quarantine.install(
            dispatch_module=dispatch,
            adapters_module=adapters,
            reliability_module=reliability,
            router_module=router,
        )
        # A positive provider balance is not enough for customer visibility when
        # the remaining amount cannot fund even the smallest request. Apply this
        # after quarantine so /models/, manual selection and the catalog share one
        # final fail-closed predicate.
        client_readiness.install(reliability)
        # Recovery of an UNKNOWN/DEGRADED/OPEN provider is stricter than routine
        # health monitoring: the paid inference path must actually answer before
        # customer traffic can see the provider again.
        provider_recovery.install(reliability)
        manual_continuity.install(router)
        auto_continuity.install(router)
