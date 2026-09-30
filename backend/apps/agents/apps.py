from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agents"
    verbose_name = "AI Agents"

    def ready(self):
        from . import schedule_models  # noqa: F401
        from . import webhook_models  # noqa: F401
        from . import signals  # noqa: F401
        # Sprint 85: complete snapshots receive project-level verification.
        from . import dev_execution_v2  # noqa: F401
        # Sprint 87: install accounting-safe cross-model Dev stage runtime once
        # per process. Existing orchestration remains in team_runtime.
        from . import team_runtime_v2  # noqa: F401
