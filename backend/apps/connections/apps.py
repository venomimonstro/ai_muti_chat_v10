from django.apps import AppConfig


class ConnectionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.connections"
    verbose_name = "External Connections"

    def ready(self):
        from django.conf import settings

        schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", None)
        if not isinstance(schedule, dict):
            return
        schedule.setdefault(
            "smm-plan-generation-sync",
            {"task": "apps.connections.tasks.sync_smm_generations", "schedule": 30.0},
        )
        schedule.setdefault(
            "smm-vk-publish-dispatch",
            {"task": "apps.connections.tasks.publish_due_smm_posts", "schedule": 60.0},
        )
