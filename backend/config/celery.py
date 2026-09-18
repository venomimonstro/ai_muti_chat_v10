import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.runtime_settings")
app = Celery("ai_workspace")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
app.conf.beat_schedule = {
    **(app.conf.beat_schedule or {}),
    "detect-abuse-hourly": {
        "task": "apps.admin_ops.tasks.detect_abuse_task",
        "schedule": 3600.0,
    },
}
