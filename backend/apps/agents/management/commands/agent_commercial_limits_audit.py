from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.agents.limits import ACTIVE_RUN_STATES, owner_concurrency_limit
from apps.agents.models import Agent, AgentRun


class Command(BaseCommand):
    help = "Audit Agent Studio commercial budgets and owner-level concurrency"

    def handle(self, *args, **options):
        failures = []
        warnings = []
        concurrency_limit = owner_concurrency_limit()

        self.stdout.write("=== AGENT COMMERCIAL LIMITS AUDIT ===")

        for agent in Agent.objects.iterator(chunk_size=200):
            run_limit = Decimal(agent.max_cost_rub_per_run)
            day_limit = Decimal(agent.max_cost_rub_per_day)
            month_limit = Decimal(agent.max_cost_rub_per_month)
            if run_limit <= 0 or day_limit <= 0 or month_limit <= 0:
                failures.append(f"agent={agent.id}: commercial limits must be positive")
                continue
            if day_limit < run_limit:
                failures.append(f"agent={agent.id}: day limit is lower than per-run limit")
            if month_limit < day_limit:
                failures.append(f"agent={agent.id}: month limit is lower than day limit")

        oversubscribed = (
            AgentRun.objects.filter(state__in=ACTIVE_RUN_STATES)
            .values("owner_id")
            .annotate(active=Count("id"))
            .filter(active__gt=concurrency_limit)
        )
        for row in oversubscribed.iterator():
            failures.append(
                f"owner={row['owner_id']}: active runs={row['active']} exceed concurrency limit={concurrency_limit}"
            )

        for run in AgentRun.objects.filter(state__in=ACTIVE_RUN_STATES).iterator(chunk_size=500):
            if Decimal(run.cost_reserved_rub) < 0 or Decimal(run.cost_actual_rub) < 0:
                failures.append(f"run={run.id}: negative commercial accounting value")
            if Decimal(run.cost_actual_rub) > 0 and Decimal(run.cost_reserved_rub) > 0:
                warnings.append(f"run={run.id}: actual and reserved cost are both non-zero; verify settlement lifecycle")

        self.stdout.write(
            f"agents={Agent.objects.count()} active_runs={AgentRun.objects.filter(state__in=ACTIVE_RUN_STATES).count()} "
            f"owner_concurrency_limit={concurrency_limit}"
        )
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"[WARN] {warning}"))
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))

        if failures:
            raise CommandError(f"Agent commercial limits audit failed: {len(failures)} problem(s)")
        self.stdout.write(self.style.SUCCESS("AGENT_COMMERCIAL_LIMITS_AUDIT_OK"))
