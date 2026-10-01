from django.apps import AppConfig


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
