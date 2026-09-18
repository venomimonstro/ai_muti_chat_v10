import io

from celery import shared_task
from django.core.management import call_command

from .recovery import recover_stale_operations


@shared_task
def recover_stale_operations_task():
    return recover_stale_operations()


@shared_task
def detect_abuse_task():
    output = io.StringIO()
    call_command("detect_abuse", stdout=output)
    return output.getvalue().strip()
