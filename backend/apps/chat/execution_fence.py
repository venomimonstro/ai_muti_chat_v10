from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError

from .models import Generation, GenerationAttempt

FENCE_ERROR_CODE = "generation_execution_fenced"


def _error_code(value) -> str:
    return str(getattr(value, "code", value) or "").strip().casefold()


def install(streaming_module) -> None:
    """Fence stale provider workers after recovery revokes their execution attempt.

    A provider call can theoretically outlive the stale-operation timeout because of
    a wedged network/client thread. Recovery may then safely close the generation and
    release/settle its reservations. If that old worker later wakes up, it must not be
    able to overwrite the recovered terminal state or settle money a second time.

    GenerationAttempt is the durable execution lease. Recovery revokes every stale
    RUNNING attempt before touching money; a worker can complete only while its exact
    attempt still owns that RUNNING lease.
    """
    raw_finish_attempt = streaming_module._finish_attempt
    if getattr(raw_finish_attempt, "_ai_workspace_execution_fence", False):
        return

    raw_record_failure = streaming_module.record_failure

    def finish_attempt(attempt, *, state, started, error=None):
        if state == GenerationAttempt.State.COMPLETED:
            current = (
                GenerationAttempt.objects.filter(pk=attempt.pk)
                .values_list("state", flat=True)
                .first()
            )
            if current != GenerationAttempt.State.RUNNING:
                raise ProviderError(
                    "Generation execution lease was revoked by recovery",
                    code=FENCE_ERROR_CODE,
                    retryable=False,
                )
        return raw_finish_attempt(
            attempt,
            state=state,
            started=started,
            error=error,
        )

    def record_failure(provider, error, *args, **kwargs):
        # A revoked local execution lease says nothing about provider health.
        if _error_code(error) == FENCE_ERROR_CODE:
            return None
        return raw_record_failure(provider, error, *args, **kwargs)

    finish_attempt._ai_workspace_execution_fence = True
    finish_attempt._raw_finish_attempt = raw_finish_attempt
    record_failure._ai_workspace_execution_fence = True
    record_failure._raw_record_failure = raw_record_failure
    streaming_module._finish_attempt = finish_attempt
    streaming_module.record_failure = record_failure

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
