from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agents"
    verbose_name = "AI Agents"

    def ready(self):
        from . import schedule_models  # noqa: F401
        from . import signals  # noqa: F401
