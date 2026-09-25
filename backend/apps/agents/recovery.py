import os
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.billing.models import BalanceReservation
from apps.billing.services import release
from apps.procurement.models import ProviderSpendReservation
from apps.procurement.services import release_provider_spend

from .models import AgentApproval, AgentRun, AgentStepRun
from .wait_runtime import wait_metadata


RECOVERABLE_STATES = (
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.REVIEWING,
)


def _timeout_seconds():
    return max(1200, int(os.getenv("AGENT_STALE_TIMEOUT_SECONDS", "1800")))


def _approval_timeout_hours():
    return max(1, int(os.getenv("AGENT_APPROVAL_TIMEOUT_HOURS", "72")))


def _cutoff():
    return timezone.now() - timedelta(seconds=_timeout_seconds())


def _approval_cutoff():
    return timezone.now() - timedelta(hours=_approval_timeout_hours())


def _release_customer_reservations(run_id):
    run_id = str(run_id)
    reservation_ids = BalanceReservation.objects.filter(
        Q(idempotency_key=f"agent-run:{run_id}")
        | Q(idempotency_key__startswith=f"agent-run:{run_id}:step:")
        | Q(idempotency_key__startswith=f"agent-team:{run_id}:step:"),
        state=BalanceReservation.State.ACTIVE,
    ).values_list("id", flat=True)
    released = 0
    for reservation_id in list(reservation_ids):
        release(reservation_id)
        released += 1
    return released


def _release_provider_reservations(run_id):
    run_id = str(run_id)
    reservation_ids = ProviderSpendReservation.objects.filter(
        Q(source_key=f"agent:{run_id}")
        | Q(source_key__startswith=f"agent:{run_id}:step:")
        | Q(source_key__startswith=f"agent-team:{run_id}:step:"),
        state=ProviderSpendReservation.State.ACTIVE,
    ).values_list("id", flat=True)
    released = 0
    for reservation_id in list(reservation_ids):
        release_provider_spend(reservation_id)
        released += 1
    return released


@transaction.atomic
def recover_agent_run(run_id):
    run = AgentRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None:
        return False
    # WAITING_TOOL is also used by the visual Agent Studio `wait` node. A
    # durable, explicitly scheduled wait is healthy state, not a dead worker.
    if run.state == AgentRun.State.WAITING_TOOL and wait_metadata(run):
        return False
    if run.state not in RECOVERABLE_STATES or run.updated_at >= _cutoff():
        return False

    released_customer = _release_customer_reservations(run.id)
    released_provider = _release_provider_reservations(run.id)
    now = timezone.now()

    run.steps.filter(state=AgentStepRun.State.RUNNING).update(
        state=AgentStepRun.State.FAILED,
        public_log="Выполнение прервано: worker не завершил шаг в допустимое время.",
        finished_at=now,
    )
    run.steps.filter(state=AgentStepRun.State.PENDING).update(
        state=AgentStepRun.State.SKIPPED,
        public_log="Шаг не запускался: предыдущая операция была восстановлена watchdog.",
        finished_at=now,
    )

    run.state = AgentRun.State.FAILED
    run.error_code = "stale_agent_run_recovered"
    run.error_message = (
        "Запуск остановлен автоматически после потери активности worker. "
        f"Освобождено резервов: user={released_customer}, provider={released_provider}. "
        "Можно безопасно запустить задачу повторно."
    )
    run.finished_at = now
    run.save(
        update_fields=[
            "state",
            "error_code",
            "error_message",
            "finished_at",
            "updated_at",
        ]
    )
    return True


@transaction.atomic
def expire_agent_approval(approval_id):
    approval = (
        AgentApproval.objects.select_for_update()
        .select_related("run")
        .filter(pk=approval_id)
        .first()
    )
    if approval is None or approval.status != AgentApproval.Status.PENDING:
        return False
    if approval.created_at >= _approval_cutoff():
        return False

    run = AgentRun.objects.select_for_update().get(pk=approval.run_id)
    now = timezone.now()
    approval.status = AgentApproval.Status.EXPIRED
    approval.decided_at = now
    approval.save(update_fields=["status", "decided_at"])

    if run.state == AgentRun.State.WAITING_APPROVAL:
        released_customer = _release_customer_reservations(run.id)
        released_provider = _release_provider_reservations(run.id)
        run.state = AgentRun.State.CANCELED
        run.error_code = "agent_approval_expired"
        run.error_message = (
            f"Подтверждение не было получено за {_approval_timeout_hours()} ч. "
            f"Действие отменено безопасно; освобождено резервов: user={released_customer}, provider={released_provider}."
        )
        run.finished_at = now
        run.save(
            update_fields=[
                "state",
                "error_code",
                "error_message",
                "finished_at",
                "updated_at",
            ]
        )
    return True


def expire_stale_agent_approvals():
    approval_ids = list(
        AgentApproval.objects.filter(
            status=AgentApproval.Status.PENDING,
            created_at__lt=_approval_cutoff(),
            run__state=AgentRun.State.WAITING_APPROVAL,
        ).values_list("id", flat=True)[:500]
    )
    expired = 0
    for approval_id in approval_ids:
        expired += int(expire_agent_approval(approval_id))
    return expired


def recover_stale_agent_runs():
    stale_ids = list(
        AgentRun.objects.filter(
            state__in=RECOVERABLE_STATES,
            updated_at__lt=_cutoff(),
        ).values_list("id", flat=True)[:500]
    )
    recovered = 0
    for run_id in stale_ids:
        recovered += int(recover_agent_run(run_id))
    return recovered
