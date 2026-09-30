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
            manual_continuity,
            model_quarantine,
            reliability,
            router,
            routing_pools,
            signals,
        )

        adapters.adapter_for = dispatch.adapter_for
        reliability.adapter_for = dispatch.adapter_for
        routing_pools.install_router_pool_resolver(router)
        model_quarantine.install(
            dispatch_module=dispatch,
            adapters_module=adapters,
            reliability_module=reliability,
            router_module=router,
        )
        manual_continuity.install(router)
        auto_continuity.install(router)
