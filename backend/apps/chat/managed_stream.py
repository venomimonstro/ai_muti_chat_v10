import logging

from django.utils import timezone

from apps.billing.services import release

from .models import Generation, Message
from .partial_billing import settle_delivered_partial
from .streaming import run

logger = logging.getLogger(__name__)


def _finalize_unhandled_disconnect(generation):
    generation.refresh_from_db(fields=["state", "reservation_id", "actual_cost_rub"])
    if generation.state not in {Generation.State.QUEUED, Generation.State.RUNNING}:
        return
    assistant = generation.assistant_message
    assistant.refresh_from_db(fields=["content", "status"])
    try:
        charge = settle_delivered_partial(generation, assistant.content)
    except Exception:
        logger.exception(
            "Managed stream cancellation settlement failed generation_id=%s",
            generation.id,
        )
        try:
            release(generation.reservation_id)
        except Exception:
            logger.exception(
                "Managed stream reservation release failed generation_id=%s",
                generation.id,
            )
        charge = 0
    assistant.status = Message.Status.PARTIAL if assistant.content else Message.Status.FAILED
    assistant.save(update_fields=["status"])
    generation.state = Generation.State.CANCELLED
    generation.error_code = "client_cancelled"
    generation.actual_cost_rub = charge
    generation.completed_at = timezone.now()
    generation.save(
        update_fields=["state", "error_code", "actual_cost_rub", "completed_at"]
    )


def managed_run(generation, *, adapter=None):
    """Wrap the entire streaming lifecycle so even a disconnect at the first SSE yield is settled."""
    try:
        yield from run(generation, adapter=adapter)
    finally:
        _finalize_unhandled_disconnect(generation)
