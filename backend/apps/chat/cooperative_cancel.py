from __future__ import annotations

import sys

from django.db import transaction
from django.utils import timezone

from apps.billing.models import BalanceReservation

from .cancellation import cancel_requested, clear_cancel, forget_cancel_probe
from .models import Generation, GenerationAttempt, Message
from .partial_billing import settle_delivered_partial
from .streaming import sse

TERMINAL_STATES = {
    Generation.State.COMPLETED,
    Generation.State.FAILED,
    Generation.State.CANCELLED,
}


@transaction.atomic
def _finalize_cancel(generation, *, clear_content: bool, queued_only: bool = False):
    locked = (
        Generation.objects.select_for_update(of=("self",))
        .select_related("assistant_message")
        .get(pk=generation.pk)
    )
    if locked.state in TERMINAL_STATES:
        return False
    if queued_only and locked.state != Generation.State.QUEUED:
        # The stream thread already owns the provider call. Leave the durable
        # cancellation marker in place so that owner thread performs settlement
        # and terminalization at the next cooperative checkpoint.
        return False

    assistant = locked.assistant_message
    if clear_content:
        assistant.content = ""
    charge = settle_delivered_partial(locked, assistant.content)
    BalanceReservation.objects.filter(
        pk=locked.reservation_id, state=BalanceReservation.State.RELEASED, actual_rub__isnull=True
    ).update(actual_rub=0)
    assistant.status = Message.Status.PARTIAL if assistant.content else Message.Status.FAILED
    assistant.save(update_fields=["content", "status"])
    now = timezone.now()
    GenerationAttempt.objects.filter(
        generation=locked,
        state=GenerationAttempt.State.RUNNING,
    ).update(
        state=GenerationAttempt.State.SKIPPED,
        error_code="client_cancelled",
        retryable=False,
        finished_at=now,
    )
    locked.state = Generation.State.CANCELLED
    locked.error_code = "client_cancelled"
    locked.actual_cost_rub = charge
    locked.completed_at = now
    locked.save(
        update_fields=["state", "error_code", "actual_cost_rub", "completed_at"]
    )
    generation.state = locked.state
    generation.error_code = locked.error_code
    generation.actual_cost_rub = locked.actual_cost_rub
    generation.completed_at = locked.completed_at
    return True


def _cancel_before_provider(generation, *, queued_only: bool = False):
    return _finalize_cancel(generation, clear_content=True, queued_only=queued_only)


def _cancel_during_stream(generation):
    """Persist authoritative partial settlement before emitting ``cancelled``."""
    return _finalize_cancel(generation, clear_content=False)


def _terminal_now(generation) -> bool:
    generation.refresh_from_db(fields=["state"])
    return generation.state in TERMINAL_STATES


def _sync_authoritative_actual(generation):
    """Make the cancellation event match the settled wallet ledger exactly."""
    generation.refresh_from_db(fields=["state", "actual_cost_rub", "reservation_id"])
    if not generation.reservation_id:
        return generation.actual_cost_rub or 0
    if generation.state == Generation.State.CANCELLED:
        BalanceReservation.objects.filter(
            pk=generation.reservation_id, state=BalanceReservation.State.RELEASED, actual_rub__isnull=True
        ).update(actual_rub=0)
    actual = (
        BalanceReservation.objects.filter(pk=generation.reservation_id)
        .values_list("actual_rub", flat=True)
        .first()
    )
    if actual is None:
        return generation.actual_cost_rub or 0
    if generation.actual_cost_rub != actual:
        Generation.objects.filter(pk=generation.pk).update(actual_cost_rub=actual)
        generation.actual_cost_rub = actual
    return actual


def _cancelled_event(generation):
    actual = _sync_authoritative_actual(generation)
    assistant = Message.objects.filter(pk=generation.assistant_message_id).only("content").first()
    partial = bool(str(getattr(assistant, "content", "") or "").strip())
    return sse(
        "cancelled",
        {
            "code": "client_cancelled",
            "generation_id": str(generation.id),
            "state": generation.state,
            "partial": partial,
            "cost_rub": str(actual or 0),
            "message": (
                "Генерация остановлена. Списана только подтверждённая стоимость уже полученной части ответа."
                if actual
                else "Запрос остановлен пользователем. Неподтверждённые расходы не списаны."
            ),
        },
    )


def install(streaming_module) -> None:
    raw_run = streaming_module.run
    if getattr(raw_run, "_ai_workspace_cooperative_cancel", False):
        return

    def run(generation, *args, **kwargs):
        try:
            generation.refresh_from_db(fields=["state"])
            if generation.state == Generation.State.CANCELLED:
                yield _cancelled_event(generation)
                return
            if cancel_requested(generation):
                try:
                    if _cancel_before_provider(generation):
                        yield _cancelled_event(generation)
                finally:
                    clear_cancel(generation)
                return

            iterator = raw_run(generation, *args, **kwargs)
            try:
                while True:
                    if cancel_requested(generation):
                        if _terminal_now(generation):
                            clear_cancel(generation)
                            if generation.state == Generation.State.CANCELLED:
                                yield _cancelled_event(generation)
                            return
                        iterator.close()
                        try:
                            finalized = _cancel_during_stream(generation)
                            generation.refresh_from_db(fields=["state"])
                            if finalized or generation.state == Generation.State.CANCELLED:
                                yield _cancelled_event(generation)
                        finally:
                            clear_cancel(generation)
                        return
                    try:
                        chunk = next(iterator)
                    except StopIteration:
                        if cancel_requested(generation):
                            generation.refresh_from_db(fields=["state"])
                            if generation.state == Generation.State.CANCELLED:
                                yield _cancelled_event(generation)
                        return
                    yield chunk
            finally:
                if cancel_requested(generation):
                    try:
                        if not _terminal_now(generation):
                            iterator.close()
                            _cancel_during_stream(generation)
                    finally:
                        clear_cancel(generation)
        finally:
            forget_cancel_probe(generation)

    run._ai_workspace_cooperative_cancel = True
    run._raw_run = raw_run
    streaming_module.run = run

    for module_name in ("apps.chat.managed_stream", "apps.chat.services", "apps.chat.views"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "run"):
            module.run = run
