from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.billing.models import BalanceReservation
from apps.billing.services import release
from apps.procurement.models import ProviderSpendReservation
from apps.procurement.services import release_provider_spend

from .models import AgentRun, AgentStepRun, AgentTeam
from .sandbox_client import destroy_workspace


RECOVERABLE_STATES = {
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.REVIEWING,
}
DEFAULT_STALE_SECONDS = 4 * 60 * 60


def stale_dev_runs(*, older_than_seconds=DEFAULT_STALE_SECONDS, now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=max(300, int(older_than_seconds)))
    return AgentRun.objects.filter(
        team__kind=AgentTeam.Kind.DEVELOPMENT,
        state__in=RECOVERABLE_STATES,
        updated_at__lt=cutoff,
    ).select_related("team", "owner")


def _release_customer_reservations(run):
    released = []
    prefix = f"agent-run:{run.id}:step:"
    reservations = BalanceReservation.objects.filter(
        wallet__user_id=run.owner_id,
        state=BalanceReservation.State.ACTIVE,
        idempotency_key__startswith=prefix,
    )
    for reservation in reservations:
        release(reservation.id)
        released.append(str(reservation.id))
    return released


def _release_provider_reservations(run):
    released = []
    prefix = f"agent:{run.id}:step:"
    reservations = ProviderSpendReservation.objects.filter(
        state=ProviderSpendReservation.State.ACTIVE,
        source_key__startswith=prefix,
    )
    for reservation in reservations:
        release_provider_spend(reservation.id)
        released.append(str(reservation.id))
    return released


def _cleanup_workspace(run):
    workspace_id = str((run.input_payload or {}).get("workspace_id") or "").strip()
    if not workspace_id:
        return {"workspace_id": "", "destroyed": False, "error": ""}
    try:
        destroy_workspace(workspace_id=workspace_id)
        return {"workspace_id": workspace_id, "destroyed": True, "error": ""}
    except Exception as exc:
        # Recovery must still terminate the stale run even if the ephemeral
        # sandbox is already gone. TTL cleanup remains the second line of defence.
        return {"workspace_id": workspace_id, "destroyed": False, "error": str(exc)[:500]}


@transaction.atomic
def recover_stale_dev_run(run_id, *, older_than_seconds=DEFAULT_STALE_SECONDS, now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=max(300, int(older_than_seconds)))
    run = (
        AgentRun.objects.select_for_update()
        .select_related("team", "owner")
        .get(pk=run_id)
    )
    if not run.team_id or run.team.kind != AgentTeam.Kind.DEVELOPMENT:
        return {"run_id": str(run.id), "recovered": False, "reason": "not_dev_run"}
    if run.state not in RECOVERABLE_STATES:
        return {"run_id": str(run.id), "recovered": False, "reason": "state_not_recoverable"}
    if run.updated_at >= cutoff:
        return {"run_id": str(run.id), "recovered": False, "reason": "run_not_stale"}

    customer_released = _release_customer_reservations(run)
    provider_released = _release_provider_reservations(run)
    workspace = _cleanup_workspace(run)

    running_steps = list(
        run.steps.filter(state=AgentStepRun.State.RUNNING).values_list("id", flat=True)
    )
    if running_steps:
        AgentStepRun.objects.filter(id__in=running_steps).update(
            state=AgentStepRun.State.FAILED,
            public_log=(
                "Шаг остановлен fail-closed recovery: worker не обновлял Dev Run в допустимое окно. "
                "Автоматический повтор запрещён, чтобы не дублировать provider/GitHub side effects."
            ),
            finished_at=now,
        )

    evidence = {
        "recovered_at": now.isoformat(),
        "previous_state": run.state,
        "customer_reservations_released": customer_released,
        "provider_reservations_released": provider_released,
        "workspace": workspace,
        "working_branch": str((run.input_payload or {}).get("working_branch") or ""),
        "running_steps_failed": [str(value) for value in running_steps],
    }
    run.input_payload = {**(run.input_payload or {}), "phase": "recovery_required", "recovery": evidence}
    run.state = AgentRun.State.FAILED
    run.error_code = "dev_runtime_interrupted"
    run.error_message = (
        "Dev Studio обнаружил зависший execution после потери worker. "
        "Запуск остановлен без автоматического повтора внешних действий; создайте новый запуск после проверки evidence."
    )
    run.finished_at = now
    run.save(
        update_fields=[
            "input_payload",
            "state",
            "error_code",
            "error_message",
            "finished_at",
            "updated_at",
        ]
    )
    return {"run_id": str(run.id), "recovered": True, **evidence}
