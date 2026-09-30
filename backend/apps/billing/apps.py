from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"

    def ready(self):
        from . import cost_policy  # noqa: F401
        from . import loss_watchdog  # noqa: F401
        from . import confirmed_usage_guard, pricing, pricing_bridge, services

        pricing_bridge.install(pricing)
        confirmed_usage_guard.install(services)
