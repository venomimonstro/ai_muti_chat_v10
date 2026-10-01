import os

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register


@register()
def procurement_production_configuration_check(app_configs, **kwargs):
    """Commercial production must never bypass the provider funding ledger."""
    if settings.DEBUG:
        return []
    if not bool(getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False)):
        return [
            Error(
                "Production procurement is permissive; provider funding can be bypassed.",
                hint="Set PROCUREMENT_RUNTIME_FAIL_CLOSED=1 in .env.production.",
                id="procurement.E001",
            )
        ]
    return []


class ProcurementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.procurement"
    verbose_name = "Закупки API"

    def ready(self):
        # Production is fail-closed by default: a commercial provider must have
        # usable purchased capacity. Dev/test stays permissive unless explicitly
        # enabled so legacy fixtures do not need artificial procurement ledgers.
        default = "false" if settings.DEBUG else "true"
        raw = os.getenv("PROCUREMENT_RUNTIME_FAIL_CLOSED", default).strip().casefold()
        settings.PROCUREMENT_RUNTIME_FAIL_CLOSED = raw not in {"0", "false", "no", "off"}

        from apps.ai_registry import reliability

        from . import (  # noqa: F401
            account_routing,
            chat_signals,
            recovery_signals,
            request_cost_guard,
            services,
            signals,
        )

        # Multiple paid API accounts for one provider are a runtime pool, not merely
        # admin metadata. Reserve one concrete healthy account per request and keep
        # provider readiness on the same account-selection contract.
        account_routing.install(
            services_module=services,
            signals_module=signals,
            reliability_module=reliability,
        )
