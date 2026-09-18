import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
app = Celery("ai_workspace")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Keep the core settings schedule intact and append commercial abuse monitoring here so
# enabling it does not require a broad settings.py rewrite.
app.conf.beat_schedule = {
    **(app.conf.beat_schedule or {}),
    "detect-abuse-hourly": {
        "task": "apps.admin_ops.tasks.detect_abuse_task",
        "schedule": 3600.0,
    },
}
