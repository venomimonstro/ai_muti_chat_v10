from celery import shared_task
from django.core.cache import cache

from .model_quarantine import recover_quarantined_models


MODEL_QUARANTINE_WATCH_LOCK = "system:model-quarantine-watch-lock"


@shared_task
def model_quarantine_watch_task():
    """Re-check quarantined upstream model ids outside customer traffic."""
    if not cache.add(MODEL_QUARANTINE_WATCH_LOCK, "1", timeout=240):
        return {"status": "skipped", "reason": "already_running"}
    try:
        result = recover_quarantined_models(limit=8)
        return {"status": "ok", **result}
    finally:
        cache.delete(MODEL_QUARANTINE_WATCH_LOCK)
