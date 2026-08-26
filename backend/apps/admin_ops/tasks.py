from celery import shared_task

from .recovery import recover_stale_operations


@shared_task
def recover_stale_operations_task():
    return recover_stale_operations()
