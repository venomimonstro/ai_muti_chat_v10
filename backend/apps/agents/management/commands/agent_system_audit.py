from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from apps.ai_registry.models import RoutingPolicyVersion
from apps.agents.models import Agent, AgentRun, AgentTeam
from apps.agents.schedule_models import AgentSchedule


ACTIVE_RUN_STATES = [
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
]


class Command(BaseCommand):
    help = "Audit Agent Studio, team routing, run invariants and autonomous schedules"

    def handle(self, *args, **options):
        failures = []
        warnings = []

        self.stdout.write("=== AGENT SYSTEM AUDIT ===")

        agents = Agent.objects.select_related("owner", "project")
        for agent in agents:
            if agent.max_cost_rub_per_run <= 0:
                failures.append(f"agent={agent.id}: max_cost_rub_per_run must be positive")
            if agent.max_steps < 1 or agent.max_tool_calls < 1 or agent.max_runtime_seconds < 1:
                failures.append(f"agent={agent.id}: execution limits must be positive")
            graph = agent.graph or {}
            nodes = graph.get("nodes") or [] if isinstance(graph, dict) else []
            edges = graph.get("edges") or [] if isinstance(graph, dict) else []
            if agent.status == Agent.Status.ACTIVE and not nodes:
                warnings.append(f"agent={agent.id}: active agent has an empty graph")
            node_ids = [str(item.get("id") or "") for item in nodes if isinstance(item, dict)]
            if len(node_ids) != len(set(node_ids)):
                failures.append(f"agent={agent.id}: graph has duplicate node ids")
            known = set(node_ids)
            for edge in edges:
                if not isinstance(edge, dict):
                    failures.append(f"agent={agent.id}: graph contains malformed edge")
                    continue
                if edge.get("from") not in known or edge.get("to") not in known:
                    failures.append(f"agent={agent.id}: graph edge references missing node")

        for team in AgentTeam.objects.select_related("owner", "director", "project").prefetch_related("members__agent"):
            members = list(team.members.all())
            enabled_members = [item for item in members if item.enabled]
            director_membership = next((item for item in members if item.agent_id == team.director_id), None)
            if team.director.owner_id != team.owner_id:
                failures.append(f"team={team.id}: director belongs to another owner")
            if director_membership is None:
                failures.append(f"team={team.id}: director is not a team member")
            elif not director_membership.enabled:
                failures.append(f"team={team.id}: director membership is disabled")
            if team.active and not enabled_members:
                failures.append(f"team={team.id}: active team has no enabled members")
            if team.max_cost_rub_per_run <= 0 or team.max_handoffs < 1:
                failures.append(f"team={team.id}: team budget/handoff limits must be positive")
            if team.kind == AgentTeam.Kind.DEVELOPMENT and not team.project_id:
                failures.append(f"team={team.id}: development team has no project")
            for membership in members:
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
                failures.append(f"schedule={schedule.id}: enabled schedule has no objective")
            if schedule.enabled and schedule.agent_id and schedule.agent.status != Agent.Status.ACTIVE:
                failures.append(f"schedule={schedule.id}: enabled schedule points to inactive agent")
            if schedule.enabled and schedule.team_id and not schedule.team.active:
                failures.append(f"schedule={schedule.id}: enabled schedule points to paused team")

        active_agents = Agent.objects.filter(status=Agent.Status.ACTIVE)
        policy = RoutingPolicyVersion.objects.filter(active=True).first()
        tier_models = ((policy.thresholds or {}).get("tier_models") or {}) if policy else {}
        for level in ("economy", "balanced", "maximum"):
            used = active_agents.filter(system_level=level).exists()
            configured = bool(str(tier_models.get(level) or "").strip())
            if used and not configured:
                failures.append(f"routing level={level}: active agents exist but no model is pinned")

        duplicate_agent_runs = (
            AgentRun.objects.filter(state__in=ACTIVE_RUN_STATES, agent__isnull=False)
            .values("owner_id", "agent_id")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
        )
        for row in duplicate_agent_runs:
            failures.append(
                f"agent={row['agent_id']}: {row['total']} simultaneous active runs for owner={row['owner_id']}"
            )

        duplicate_team_runs = (
            AgentRun.objects.filter(state__in=ACTIVE_RUN_STATES, team__isnull=False)
            .values("owner_id", "team_id")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
        )
        for row in duplicate_team_runs:
            failures.append(
                f"team={row['team_id']}: {row['total']} simultaneous active runs for owner={row['owner_id']}"
            )

        stale_cutoff = timezone.now() - timedelta(hours=3)
        stale_states = [
            AgentRun.State.QUEUED,
            AgentRun.State.PLANNING,
            AgentRun.State.RUNNING,
            AgentRun.State.WAITING_TOOL,
            AgentRun.State.REVIEWING,
        ]
        for run in AgentRun.objects.filter(state__in=stale_states, updated_at__lt=stale_cutoff)[:100]:
            warnings.append(f"run={run.id}: state={run.state} has not changed for more than 3 hours")

        active_runs = AgentRun.objects.filter(state__in=ACTIVE_RUN_STATES).count()

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
