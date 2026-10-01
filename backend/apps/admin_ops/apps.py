from django.apps import AppConfig


class AdminOpsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.admin_ops"
    verbose_name = "Администрирование и операции"

    def ready(self):
        from . import issue_models  # noqa: F401
        from . import diagnostics_chat_service, diagnostics_views, procurement_views, search_economics

        search_economics.install(procurement_views)
        diagnostics_chat_service.install(diagnostics_views)
