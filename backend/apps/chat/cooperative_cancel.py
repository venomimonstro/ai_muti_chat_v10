from __future__ import annotations

import sys

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


def _finalize_cancel(generation, *, clear_content: bool):
    generation.refresh_from_db(fields=["state"])
    if generation.state in TERMINAL_STATES:
        return False
    assistant = generation.assistant_message
    assistant.refresh_from_db(fields=["content", "status"])
    if clear_content:
        assistant.content = ""
    charge = settle_delivered_partial(generation, assistant.content)
    assistant.status = Message.Status.PARTIAL if assistant.content else Message.Status.FAILED
    assistant.save(update_fields=["content", "status"])
    now = timezone.now()
    GenerationAttempt.objects.filter(
        generation=generation,
        state=GenerationAttempt.State.RUNNING,
    ).update(
        state=GenerationAttempt.State.SKIPPED,
        error_code="client_cancelled",
        retryable=False,
        finished_at=now,
    )
    generation.state = Generation.State.CANCELLED
    generation.error_code = "client_cancelled"
    generation.actual_cost_rub = charge
    generation.completed_at = now
    generation.save(
        update_fields=["state", "error_code", "actual_cost_rub", "completed_at"]
    )
    return True


def _cancel_before_provider(generation):
    return _finalize_cancel(generation, clear_content=True)


def _cancel_during_stream(generation):
    """Persist authoritative partial settlement before emitting ``cancelled``.

    The old path only closed the provider iterator and emitted an SSE event. The
    outer managed-stream finalizer then saw a still-RUNNING generation and changed
    it to FAILED/stream_incomplete. A client-visible ``cancelled`` event must be a
    durable terminal state, otherwise history, diagnostics and billing disagree.
    """
    return _finalize_cancel(generation, clear_content=False)


def _terminal_now(generation) -> bool:
    generation.refresh_from_db(fields=["state"])
    return generation.state in TERMINAL_STATES


def _sync_authoritative_actual(generation):
    """Make the cancellation event match the settled wallet ledger exactly."""
    generation.refresh_from_db(fields=["state", "actual_cost_rub", "reservation_id"])
    if not generation.reservation_id:
        return generation.actual_cost_rub or 0
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
            if cancel_requested(generation):
                try:
                    if _cancel_before_provider(generation):
                        yield _cancelled_event(generation)
                finally:
                    clear_cancel(generation)
                return

            iterator = raw_run(generation, *args, **kwargs)
            try:
                for chunk in iterator:
                    if cancel_requested(generation):
                        if _terminal_now(generation):
                            clear_cancel(generation)
                            yield chunk
                            continue
                        iterator.close()
                        try:
                            if _cancel_during_stream(generation):
                                yield _cancelled_event(generation)
                        finally:
                            clear_cancel(generation)
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
