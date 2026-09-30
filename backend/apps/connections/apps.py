from django.apps import AppConfig


class ConnectionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.connections"
    verbose_name = "External Connections"

    def ready(self):
        from . import smm_models  # noqa: F401
