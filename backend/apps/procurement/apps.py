import os

from django.apps import AppConfig
from django.conf import settings


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

        from . import chat_signals, signals  # noqa: F401
