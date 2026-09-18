from django.apps import AppConfig


class AdminOpsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.admin_ops"
    verbose_name = "Администрирование и операции"

    def ready(self):
        from . import issue_models  # noqa: F401
