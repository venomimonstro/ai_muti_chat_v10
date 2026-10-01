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
            dispatch,
            http_errors,
            manual_continuity,
            model_quarantine,
            provider_recovery,
            reliability,
            router,
            router_intelligence,
            routing_pools,
            signals,
        )

        http_errors.install(adapters)
        adapters.adapter_for = dispatch.adapter_for
        reliability.adapter_for = dispatch.adapter_for
        routing_pools.install_router_pool_resolver(router)
        # Router v3 keeps reasoning complexity independent from web/tool usage.
        # Continuity is installed afterwards so fallback uses the same preferred
        # tier as the primary AUTO decision.
        router_intelligence.install(router)
        model_quarantine.install(
            dispatch_module=dispatch,
            adapters_module=adapters,
            reliability_module=reliability,
            router_module=router,
        )
        # Recovery of an UNKNOWN/DEGRADED/OPEN provider is stricter than routine
        # health monitoring: the paid inference path must actually answer before
        # customer traffic can see the provider again.
        provider_recovery.install(reliability)
        manual_continuity.install(router)
        auto_continuity.install(router)
