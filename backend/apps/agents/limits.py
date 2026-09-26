from datetime import datetime, time
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from .models import AgentRun, AgentStepRun

ZERO = Decimal("0")
ACTIVE_RUN_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}


def _period_start(*, month=False):
    now = timezone.localtime()
    day = now.date().replace(day=1) if month else now.date()
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())


def _sum_cost(queryset):
    value = queryset.aggregate(total=Sum("cost_rub"))["total"] or ZERO
    return Decimal(value)


def owner_concurrency_limit():
    return max(1, int(getattr(settings, "AGENT_MAX_ACTIVE_RUNS_PER_USER", 3)))


def owner_active_run_count(owner, *, exclude_run_id=None):
    queryset = AgentRun.objects.filter(owner=owner, state__in=ACTIVE_RUN_STATES)
    if exclude_run_id:
        queryset = queryset.exclude(pk=exclude_run_id)
    return queryset.count()


def ensure_owner_run_capacity(owner, *, exclude_run_id=None):
    limit = owner_concurrency_limit()
    active = owner_active_run_count(owner, exclude_run_id=exclude_run_id)
    if active >= limit:
        raise ValidationError(
            f"Достигнут лимит одновременных запусков AI-сотрудников: {active}/{limit}. "
            "Дождитесь завершения или остановите один из активных запусков."
        )
    return {"active_runs": active, "concurrency_limit": limit, "available_slots": limit - active}


def agent_spend_since(agent, since):
    return _sum_cost(
        AgentStepRun.objects.filter(
            agent=agent,
            created_at__gte=since,
            state=AgentStepRun.State.COMPLETED,
        )
    )


def agent_budget_snapshot(agent, run=None):
    day_spend = agent_spend_since(agent, _period_start(month=False))
    month_spend = agent_spend_since(agent, _period_start(month=True))
    run_spend = ZERO
    if run is not None:
        run_spend = _sum_cost(
            AgentStepRun.objects.filter(
                run=run,
                agent=agent,
                state=AgentStepRun.State.COMPLETED,
            )
        )
    run_limit = Decimal(agent.max_cost_rub_per_run)
    day_limit = Decimal(agent.max_cost_rub_per_day)
    month_limit = Decimal(agent.max_cost_rub_per_month)
    return {
        "run_spend": run_spend,
        "day_spend": day_spend,
        "month_spend": month_spend,
        "run_limit": run_limit,
        "day_limit": day_limit,
        "month_limit": month_limit,
        "run_remaining": max(ZERO, run_limit - run_spend),
        "day_remaining": max(ZERO, day_limit - day_spend),
        "month_remaining": max(ZERO, month_limit - month_spend),
    }


def agent_commercial_snapshot(agent, *, run=None):
    budget = agent_budget_snapshot(agent, run=run)
    concurrency_limit = owner_concurrency_limit()
    active_runs = owner_active_run_count(agent.owner)
    effective_remaining = min(
        budget["run_remaining"],
        budget["day_remaining"],
        budget["month_remaining"],
    )
    return {
        **budget,
        "effective_remaining": effective_remaining,
        "active_runs": active_runs,
        "concurrency_limit": concurrency_limit,
        "available_slots": max(0, concurrency_limit - active_runs),
        "can_start": effective_remaining > ZERO and active_runs < concurrency_limit,
    }


def effective_remaining_budget(agent, run=None):
    snapshot = agent_budget_snapshot(agent, run=run)
    return min(
        snapshot["run_remaining"],
        snapshot["day_remaining"],
        snapshot["month_remaining"],
    ), snapshot
