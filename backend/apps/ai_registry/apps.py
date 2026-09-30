from django.apps import AppConfig


class AIRegistryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ai_registry"

    def ready(self):
        # Install fail-safe runtime dispatch and the authoritative routing-pool
        # resolver once per process. Modules that imported adapter_for directly
        # before AppConfig.ready() must also be rebound explicitly.
        from . import (  # noqa: F401
            adapters,
            dispatch,
            manual_continuity,
            reliability,
            router,
            routing_pools,
            signals,
        )

        adapters.adapter_for = dispatch.adapter_for
        reliability.adapter_for = dispatch.adapter_for
        routing_pools.install_router_pool_resolver(router)
        manual_continuity.install(router)
