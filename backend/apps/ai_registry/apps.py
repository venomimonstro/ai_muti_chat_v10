from django.apps import AppConfig


class AIRegistryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ai_registry"

    def ready(self):
        # Install fail-safe runtime dispatch and the authoritative routing-pool
        # resolver once per process. Importing here avoids touching Django models
        # before the app registry is ready.
        from . import adapters, dispatch, router, routing_pools, signals  # noqa: F401

        adapters.adapter_for = dispatch.adapter_for
        routing_pools.install_router_pool_resolver(router)
