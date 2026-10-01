import os

from celery import Celery
from celery.signals import task_failure

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.runtime_settings")
app = Celery("ai_workspace")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
app.conf.beat_schedule = {
    **(app.conf.beat_schedule or {}),
    "system-worker-heartbeat": {
        "task": "apps.admin_ops.tasks.system_heartbeat_task",
        "schedule": 60.0,
    },
    "chat-service-health-watch": {
        "task": "apps.chat.tasks.chat_service_health_watch_task",
        "schedule": 60.0,
    },
    "recover-quarantined-ai-models": {
        "task": "apps.ai_registry.tasks.model_quarantine_watch_task",
        "schedule": 300.0,
    },
    "recover-stale-operations": {
        "task": "apps.admin_ops.tasks.recover_stale_operations_task",
        "schedule": 300.0,
    },
    "reconcile-payments-refunds": {
        "task": "apps.admin_ops.tasks.payment_reconciliation_task",
        "schedule": 300.0,
    },
    "economic-safety-watch": {
        "task": "apps.admin_ops.tasks.economic_safety_watch_task",
        "schedule": 300.0,
    },
    "billing-ledger-integrity-watch": {
        "task": "apps.admin_ops.tasks.billing_integrity_watch_task",
        "schedule": 900.0,
    },
    "official-ai-pricing-daily": {
        "task": "apps.admin_ops.tasks.official_pricing_sync_task",
        "schedule": 86400.0,
    },
    "detect-abuse-hourly": {
        "task": "apps.admin_ops.tasks.detect_abuse_task",
        "schedule": 3600.0,
    },
    "support-sla-watch": {
        "task": "apps.admin_ops.tasks.support_sla_watch_task",
        "schedule": 900.0,
    },
    "dispatch-autonomous-agents": {
        "task": "apps.agents.tasks.dispatch_due_agent_schedules",
        "schedule": 60.0,
    },
    "check-external-connections": {
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
