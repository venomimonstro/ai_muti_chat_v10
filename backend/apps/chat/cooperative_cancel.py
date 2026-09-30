from __future__ import annotations

import sys

from django.utils import timezone

from .cancellation import cancel_requested, clear_cancel
from .models import Generation, Message
from .partial_billing import settle_delivered_partial
from .streaming import sse


def _cancel_before_provider(generation):
    charge = settle_delivered_partial(generation, "")
    assistant = generation.assistant_message
    assistant.content = ""
    assistant.status = Message.Status.FAILED
    assistant.save(update_fields=["content", "status"])
    generation.state = Generation.State.CANCELLED
    generation.error_code = "client_cancelled"
    generation.actual_cost_rub = charge
    generation.completed_at = timezone.now()
    generation.save(
        update_fields=["state", "error_code", "actual_cost_rub", "completed_at"]
    )


def install(streaming_module) -> None:
    raw_run = streaming_module.run
    if getattr(raw_run, "_ai_workspace_cooperative_cancel", False):
        return

    def run(generation, *args, **kwargs):
        # Covers the race where Stop arrives while prepare() is still committing.
        if cancel_requested(generation):
            try:
                _cancel_before_provider(generation)
                yield sse(
                    "error",
                    {
                        "code": "client_cancelled",
                        "partial": False,
                        "cost_rub": "0",
                        "message": "Запрос остановлен пользователем. Неподтверждённые расходы не списаны.",
                    },
                )
            finally:
                clear_cancel(generation)
            return

        iterator = raw_run(generation, *args, **kwargs)
        try:
            for chunk in iterator:
                if cancel_requested(generation):
                    # Closing raw streaming.run enters its authoritative GeneratorExit
                    # settlement path. It alone decides whether confirmed usage exists.
                    iterator.close()
                    clear_cancel(generation)
                    return
                yield chunk
        finally:
            if cancel_requested(generation):
                try:
                    iterator.close()
                finally:
                    clear_cancel(generation)

    run._ai_workspace_cooperative_cancel = True
    run._raw_run = raw_run
    streaming_module.run = run

    # Modules can import run by value. Keep every known customer execution surface
    # aligned so WSGI tests and ASGI production use the same cancellation contract.
    for module_name in ("apps.chat.managed_stream", "apps.chat.services", "apps.chat.views"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "run"):
            module.run = run
