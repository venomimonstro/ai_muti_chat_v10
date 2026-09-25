from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.ai_registry.models import RoutingPolicyVersion
from apps.agents.models import Agent, AgentRun, AgentTeam
from apps.agents.schedule_models import AgentSchedule


class Command(BaseCommand):
    help = "Audit Agent Studio, team routing and autonomous schedules"

    def handle(self, *args, **options):
        failures = []
        warnings = []

        self.stdout.write("=== AGENT SYSTEM AUDIT ===")

        bad_team_owners = AgentTeam.objects.exclude(director__owner_id=models_owner_id()).count() if False else 0
        # Explicit queryset loops keep this command compatible with SQLite test environments too.
        for team in AgentTeam.objects.select_related("owner", "director", "project").prefetch_related("members__agent"):
            if team.director.owner_id != team.owner_id:
                failures.append(f"team={team.id}: director belongs to another owner")
            if team.kind == AgentTeam.Kind.DEVELOPMENT and not team.project_id:
                failures.append(f"team={team.id}: development team has no project")
            for membership in team.members.all():
                if membership.agent.owner_id != team.owner_id:
                    failures.append(f"team={team.id}: member={membership.agent_id} belongs to another owner")
                if team.project_id and membership.agent.project_id not in {None, team.project_id}:
                    failures.append(f"team={team.id}: member={membership.agent_id} belongs to another project")

        for schedule in AgentSchedule.objects.select_related("owner", "agent", "team"):
            subject = schedule.agent or schedule.team
            if subject is None:
                failures.append(f"schedule={schedule.id}: no subject")
                continue
            if subject.owner_id != schedule.owner_id:
                failures.append(f"schedule={schedule.id}: owner mismatch")
            if schedule.interval_minutes < 5:
                failures.append(f"schedule={schedule.id}: interval below 5 minutes")
            objective = (schedule.objective or getattr(subject, "objective", "") or "").strip()
            if schedule.enabled and not objective:
                warnings.append(f"schedule={schedule.id}: enabled but has no objective")

        active_agents = Agent.objects.filter(status=Agent.Status.ACTIVE)
        policy = RoutingPolicyVersion.objects.filter(active=True).first()
        tier_models = ((policy.thresholds or {}).get("tier_models") or {}) if policy else {}
        for level in ("economy", "balanced", "maximum"):
            used = active_agents.filter(system_level=level).exists()
            configured = bool(str(tier_models.get(level) or "").strip())
            if used and not configured:
                failures.append(f"routing level={level}: active agents exist but no model is pinned")

        active_runs = AgentRun.objects.filter(
            state__in=[
                AgentRun.State.QUEUED,
                AgentRun.State.PLANNING,
                AgentRun.State.RUNNING,
                AgentRun.State.WAITING_TOOL,
                AgentRun.State.WAITING_APPROVAL,
                AgentRun.State.REVIEWING,
            ]
        ).count()

        self.stdout.write(
            f"agents={Agent.objects.count()} teams={AgentTeam.objects.count()} schedules={AgentSchedule.objects.count()} active_runs={active_runs}"
        )
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"[WARN] {warning}"))
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))

        if failures:
            raise CommandError(f"Agent system audit failed: {len(failures)} problem(s)")
        self.stdout.write(self.style.SUCCESS("AGENT_SYSTEM_AUDIT_OK"))


def models_owner_id():
    # Kept out of queryset expressions intentionally; see compatibility note in handle().
    return None
