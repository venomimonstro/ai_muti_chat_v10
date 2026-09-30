import os

from django.apps import AppConfig
from django.conf import settings


class ProcurementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.procurement"
    verbose_name = "Закупки API"

    def ready(self):
        # The AI aggregator is a commercial runtime: once provider procurement is
        # used, routing must not silently ignore an exhausted or missing purchased
        # balance. Keep an explicit emergency opt-out for migrations/dev only.
        raw = os.getenv("PROCUREMENT_RUNTIME_FAIL_CLOSED", "true").strip().casefold()
        settings.PROCUREMENT_RUNTIME_FAIL_CLOSED = raw not in {"0", "false", "no", "off"}

        from . import signals  # noqa: F401
