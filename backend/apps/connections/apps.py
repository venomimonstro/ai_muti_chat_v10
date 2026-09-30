from django.apps import AppConfig


class ConnectionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.connections"
    verbose_name = "External Connections"

    def ready(self):
        from django.conf import settings
        from . import smm_models  # noqa: F401

        schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", None)
        if isinstance(schedule, dict):
            schedule.setdefault(
                "smm-vk-publish-dispatch",
                {"task": "apps.connections.tasks.publish_due_smm_posts", "schedule": 60.0},
            )
