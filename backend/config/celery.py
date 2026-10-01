import os

from celery import Celery
from celery.signals import task_failure

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.runtime_settings")
app = Celery("ai_workspace")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# CELERY_BEAT_SCHEDULE in settings.py is the authoritative baseline. Only add truly
# new schedules here or override an existing key deliberately. Using a second name for
# the same task creates duplicate broker traffic/worker load even when the task itself
# has a distributed lock.
app.conf.beat_schedule = {
    **(app.conf.beat_schedule or {}),
    "chat-service-health-watch": {
        "task": "apps.chat.tasks.chat_service_health_watch_task",
        "schedule": 60.0,
    },
    # Keep the existing recovery key/cadence. recover_stale_operations_task already
    # performs quarantined-model recovery, so no second model-recovery beat job exists.
    "recover-stale-operations": {
        "task": "apps.admin_ops.tasks.recover_stale_operations_task",
        "schedule": 300.0,
    },
    # External connections need a faster product-facing health cadence than the old
    # six-hour baseline. Override the SAME key instead of scheduling the same task twice.
    "external-connection-health-watch": {
        "task": "apps.connections.tasks.check_external_connections",
        "schedule": 300.0,
    },
    "sync-smm-generations": {
        "task": "apps.connections.tasks.sync_smm_generations",
        "schedule": 30.0,
    },
    "publish-due-smm-posts": {
        "task": "apps.connections.tasks.publish_due_smm_posts",
        "schedule": 60.0,
    },
}


@task_failure.connect
def capture_task_failure(sender=None, task_id=None, exception=None, **_kwargs):
    if exception is None:
        return
    try:
        from apps.admin_ops.system_health import record_background_exception

        record_background_exception(
            task_name=getattr(sender, "name", "unknown"),
            task_id=str(task_id or ""),
            exc=exception,
        )
    except Exception:
        return
