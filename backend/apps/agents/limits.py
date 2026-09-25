from datetime import datetime, time
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import AgentStepRun

ZERO = Decimal("0")


def _period_start(*, month=False):
    now = timezone.localtime()
    day = now.date().replace(day=1) if month else now.date()
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())


def _sum_cost(queryset):
    value = queryset.aggregate(total=Sum("cost_rub"))["total"] or ZERO
    return Decimal(value)


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


def effective_remaining_budget(agent, run=None):
    snapshot = agent_budget_snapshot(agent, run=run)
    return min(
        snapshot["run_remaining"],
        snapshot["day_remaining"],
        snapshot["month_remaining"],
    ), snapshot
