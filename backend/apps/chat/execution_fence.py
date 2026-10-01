from __future__ import annotations

import sys

from django.db import transaction
from django.utils import timezone

from .models import Generation, GenerationAttempt

FENCE_ERROR_CODE = "generation_execution_fenced"


class GenerationExecutionFenced(BaseException):
    """Internal control-flow signal: recovery has revoked this worker's lease."""

    code = FENCE_ERROR_CODE


def install(streaming_module) -> None:
    """Fence stale provider workers after recovery revokes their execution attempt.

    ``GenerationAttempt`` is the durable execution lease. Recovery revokes stale
    RUNNING attempts before touching money. A worker that wakes afterwards raises a
    non-Provider control-flow signal, so it cannot enter provider retry/fallback or
    poison provider health.
    """
    raw_finish_attempt = streaming_module._finish_attempt
    if not getattr(raw_finish_attempt, "_ai_workspace_execution_fence", False):

        def finish_attempt(attempt, *, state, started, error=None):
            if state == GenerationAttempt.State.COMPLETED:
                current = (
                    GenerationAttempt.objects.filter(pk=attempt.pk)
                    .values_list("state", flat=True)
                    .first()
                )
                if current != GenerationAttempt.State.RUNNING:
                    raise GenerationExecutionFenced(
                        "Generation execution lease was revoked by recovery"
                    )
            return raw_finish_attempt(
                attempt,
                state=state,
                started=started,
                error=error,
            )

        finish_attempt._ai_workspace_execution_fence = True
        finish_attempt._raw_finish_attempt = raw_finish_attempt
        streaming_module._finish_attempt = finish_attempt

    # Patch the stale recovery worker in the same process. The recovery function
    # remains the billing authority; this wrapper only revokes stale execution leases
    # immediately before that authority closes the generation.
    from apps.admin_ops import recovery as recovery_module

    raw_recover_generation = recovery_module._recover_generation
    if getattr(raw_recover_generation, "_ai_workspace_execution_fence", False):
        return

    def recover_generation(pk):
        cutoff = recovery_module._cutoff()
        with transaction.atomic():
            generation = Generation.objects.select_for_update().filter(pk=pk).first()
            if generation is None:
                return False
            if generation.state not in {
                Generation.State.QUEUED,
                Generation.State.RUNNING,
            } or generation.created_at >= cutoff:
                return False
            if GenerationAttempt.objects.filter(
                generation=generation,
                state=GenerationAttempt.State.RUNNING,
                started_at__gte=cutoff,
            ).exists():
                return False

            now = timezone.now()
            GenerationAttempt.objects.filter(
                generation=generation,
                state=GenerationAttempt.State.RUNNING,
            ).update(
                state=GenerationAttempt.State.FAILED,
                error_code="stale_operation_recovered",
                retryable=False,
                finished_at=now,
            )

        return raw_recover_generation(pk)

    recover_generation._ai_workspace_execution_fence = True
    recover_generation._raw_recover_generation = raw_recover_generation
    recovery_module._recover_generation = recover_generation


def install_outer_guard(streaming_module, managed_stream_module) -> None:
    """Swallow a revoked worker only after recovery made the Generation terminal."""
    raw_run = streaming_module.run
    if getattr(raw_run, "_ai_workspace_execution_fence_outer", False):
        return

    def run(generation, *args, **kwargs):
        try:
            yield from raw_run(generation, *args, **kwargs)
        except GenerationExecutionFenced:
            generation.refresh_from_db(fields=["state"])
            if generation.state not in {
                Generation.State.QUEUED,
                Generation.State.RUNNING,
            }:
                return
            # A fence signal without a terminal recovery state indicates an internal
            # invariant violation. Never silently hide it.
            raise

    run._ai_workspace_execution_fence_outer = True
    run._raw_run = raw_run
    for name, value in getattr(raw_run, "__dict__", {}).items():
        if name.startswith("_ai_workspace_"):
            setattr(run, name, value)

    streaming_module.run = run
    managed_stream_module.run = run

    for module_name in ("apps.chat.services", "apps.chat.views"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "run"):
            module.run = run
