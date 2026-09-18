import io

from celery import shared_task
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from .recovery import recover_stale_operations

WORKER_HEARTBEAT_KEY = "system:celery-worker-heartbeat"


@shared_task
def system_heartbeat_task():
    now = timezone.now().isoformat()
    cache.set(WORKER_HEARTBEAT_KEY, now, timeout=180)
    return now


@shared_task
def recover_stale_operations_task():
    return recover_stale_operations()


@shared_task
def detect_abuse_task():
    output = io.StringIO()
    call_command("detect_abuse", stdout=output)
    return output.getvalue().strip()
