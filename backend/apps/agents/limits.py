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


def agent_spend_since(agent, since):
    value = (
        AgentStepRun.objects.filter(
            agent=agent,
            created_at__gte=since,
            state=AgentStepRun.State.COMPLETED,
        ).aggregate(total=Sum("cost_rub"))["total"]
        or ZERO
    )
    return Decimal(value)


def agent_budget_snapshot(agent):
    day_spend = agent_spend_since(agent, _period_start(month=False))
    month_spend = agent_spend_since(agent, _period_start(month=True))
    day_limit = Decimal(agent.max_cost_rub_per_day)
    month_limit = Decimal(agent.max_cost_rub_per_month)
    return {
        "day_spend": day_spend,
        "month_spend": month_spend,
        "day_limit": day_limit,
        "month_limit": month_limit,
        "day_remaining": max(ZERO, day_limit - day_spend),
        "month_remaining": max(ZERO, month_limit - month_spend),
    }


def effective_run_budget(agent):
    snapshot = agent_budget_snapshot(agent)
    per_run = Decimal(agent.max_cost_rub_per_run)
    return min(per_run, snapshot["day_remaining"], snapshot["month_remaining"]), snapshot
