import os
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.billing.models import BalanceReservation
from apps.billing.services import release
from apps.procurement.models import ProviderSpendReservation
from apps.procurement.services import release_provider_spend

from .models import AgentRun, AgentStepRun


RECOVERABLE_STATES = (
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.REVIEWING,
)


def _timeout_seconds():
    # Agent workers have their own hard time limit. Keep recovery comfortably
    # above it so a slow but live worker is never raced by the watchdog.
    return max(1200, int(os.getenv("AGENT_STALE_TIMEOUT_SECONDS", "1800")))


def _cutoff():
    return timezone.now() - timedelta(seconds=_timeout_seconds())


def _release_customer_reservations(run_id):
    prefix = f"agent-run:{run_id}:step:"
    reservation_ids = BalanceReservation.objects.filter(
        idempotency_key__startswith=prefix,
        state=BalanceReservation.State.ACTIVE,
    ).values_list("id", flat=True)
    released = 0
    for reservation_id in list(reservation_ids):
        release(reservation_id)
        released += 1
    return released


def _release_provider_reservations(run_id):
    prefix = f"agent:{run_id}:step:"
    reservation_ids = ProviderSpendReservation.objects.filter(
        source_key__startswith=prefix,
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
