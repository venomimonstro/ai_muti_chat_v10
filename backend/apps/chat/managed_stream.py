import json
import logging

from django.utils import timezone

from apps.billing.models import BalanceReservation
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
            closed = release(generation.reservation_id)
            charge = closed.actual_rub or 0
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


def _reservation_actual(generation):
    if not generation.reservation_id:
        return None
    return BalanceReservation.objects.filter(pk=generation.reservation_id).values_list(
        "actual_rub", flat=True
    ).first()


def _rewrite_error_chunk_if_needed(generation, chunk):
    """Keep the SSE billing message consistent with the durable ledger.

    streaming.run() can fail after authoritative provider usage has already been
    persisted. release() then safely settles the exact snapshotted amount. The
    legacy SSE text always said "money was not charged", which is incorrect in
    that recovery case and can create a support/financial dispute.
    """
    if not isinstance(chunk, str) or not chunk.startswith("event: error\n"):
        return chunk
    actual = _reservation_actual(generation)
    if actual is None:
        return chunk
    generation.refresh_from_db(fields=["state", "actual_cost_rub"])
    if generation.actual_cost_rub != actual:
        Generation.objects.filter(pk=generation.pk).update(actual_cost_rub=actual)
        generation.actual_cost_rub = actual
    try:
        data_line = next(
            line for line in chunk.splitlines() if line.startswith("data: ")
        )
        payload = json.loads(data_line[6:])
    except Exception:
        return chunk
    if actual > 0:
        payload["cost_rub"] = str(actual)
        payload["message"] = (
            "Запрос прервался после подтверждённого расхода LLM. "
            f"Списана только подтверждённая стоимость {actual} ₽; остаток резерва возвращён."
        )
    else:
        payload["cost_rub"] = "0"
        payload["message"] = "Запрос прервался до подтверждения расхода LLM. Деньги не списаны."
    return f"event: error\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def managed_run(generation, *, adapter=None):
    """Wrap the entire streaming lifecycle so disconnects and error billing stay durable."""
    try:
        for chunk in run(generation, adapter=adapter):
            yield _rewrite_error_chunk_if_needed(generation, chunk)
    finally:
        _finalize_unhandled_disconnect(generation)
