from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone

from apps.ai_registry.models import RoutingPolicyVersion
from apps.agents.models import Agent, AgentApproval, AgentRun, AgentTeam
from apps.agents.schedule_models import AgentSchedule


ACTIVE_RUN_STATES = [
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
]

DEV_REQUIRED_ROLES = {"Engineering Director", "Architecture", "Development", "QA & Security"}
PROMPT_NODE_TYPES = {"llm", "research", "web", "files", "image", "review", "analytics"}


def _ancestor_node_ids(edges, node_id):
    reverse = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("from") or "").strip()
        target = str(edge.get("to") or "").strip()
        if source and target:
            reverse.setdefault(target, set()).add(source)
    ancestors = set()
    stack = list(reverse.get(str(node_id), set()))
    while stack:
        current = stack.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        stack.extend(reverse.get(current, set()))
    return ancestors


def _tool_value(agent, key):
    return (agent.tool_policy or {}).get(key)


class Command(BaseCommand):
    help = "Audit Agent Studio, Dev Studio, team routing, run invariants and autonomous schedules"

    def handle(self, *args, **options):
        failures = []
        warnings = []

        self.stdout.write("=== AGENT SYSTEM AUDIT ===")

        beat_tasks = {
            str((entry or {}).get("task") or "")
            for entry in getattr(settings, "CELERY_BEAT_SCHEDULE", {}).values()
            if isinstance(entry, dict)
        }
        required_beat_tasks = {
            "apps.agents.tasks.dispatch_due_agent_schedules": "schedule dispatcher",
            "apps.agents.tasks.expire_stale_agent_approvals": "approval expiry watchdog",
            "apps.connections.tasks.check_external_connections": "external connection health watchdog",
            "apps.admin_ops.tasks.recover_stale_operations_task": "stale-operation watchdog",
        }
        for task_name, label in required_beat_tasks.items():
            if task_name not in beat_tasks:
                failures.append(f"autonomy: {label} is not configured in Celery Beat")
        if int(getattr(settings, "AGENT_APPROVAL_TIMEOUT_HOURS", 0)) < 1:
            failures.append("autonomy: AGENT_APPROVAL_TIMEOUT_HOURS must be positive")

        agents = Agent.objects.select_related("owner", "project")
        for agent in agents:
            if agent.max_cost_rub_per_run <= 0:
                failures.append(f"agent={agent.id}: max_cost_rub_per_run must be positive")
            if agent.max_steps < 1 or agent.max_tool_calls < 1 or agent.max_runtime_seconds < 1:
                failures.append(f"agent={agent.id}: execution limits must be positive")
            graph = agent.graph if isinstance(agent.graph, dict) else {}
            nodes = [item for item in (graph.get("nodes") or []) if isinstance(item, dict)]
            edges = [item for item in (graph.get("edges") or []) if isinstance(item, dict)]
            if agent.status == Agent.Status.ACTIVE and not nodes:
                warnings.append(f"agent={agent.id}: active agent has an empty graph")
            node_ids = [str(item.get("id") or "").strip() for item in nodes]
            if any(not node_id for node_id in node_ids):
                failures.append(f"agent={agent.id}: graph contains node without id")
            if len(node_ids) != len(set(node_ids)):
                failures.append(f"agent={agent.id}: graph has duplicate node ids")
            known = set(node_ids)
            positions = {node_id: index for index, node_id in enumerate(node_ids)}
            for edge in edges:
                source = str(edge.get("from") or "").strip()
                target = str(edge.get("to") or "").strip()
                if source not in known or target not in known:
                    failures.append(f"agent={agent.id}: graph edge references missing node")
                    continue
                if positions.get(target, -1) <= positions.get(source, -1):
                    failures.append(
                        f"agent={agent.id}: graph edge {source}->{target} is backward/cyclic; only forward routes are allowed"
                    )

            for index, node in enumerate(nodes):
                node_id = node_ids[index]
                node_type = str(node.get("type") or "llm").strip().lower()
                if node_type == "condition":
                    for branch_name in ("on_true", "on_false"):
                        target = str(node.get(branch_name) or "").strip()
                        if not target:
                            continue
                        if target not in known:
                            failures.append(f"agent={agent.id}: condition node={node_id} points to missing target={target}")
                        elif positions[target] <= index:
                            failures.append(
                                f"agent={agent.id}: condition node={node_id} has backward target={target}; only forward routes are allowed"
                            )
                if node_type == "wait":
                    try:
                        wait_minutes = int(node.get("wait_minutes") or 60)
                    except (TypeError, ValueError):
                        wait_minutes = 0
                    if wait_minutes < 1 or wait_minutes > 10080:
                        failures.append(f"agent={agent.id}: wait node={node_id} must be between 1 and 10080 minutes")
                if node_type in PROMPT_NODE_TYPES and not str(node.get("prompt") or "").strip():
                    warnings.append(f"agent={agent.id}: node={node_id} type={node_type} has no step instruction")

            if agent.status == Agent.Status.ACTIVE:
                publish_policy = str((agent.tool_policy or {}).get("publish") or "").strip().lower()
                node_types = {
                    str(item.get("id") or ""): str(item.get("type") or "").strip().lower()
                    for item in nodes
                }
                publish_nodes = [node_id for node_id, node_type in node_types.items() if node_type == "publish"]
                if publish_nodes and publish_policy in {"auto", "autonomous", "true"} and agent.autonomy != Agent.Autonomy.AUTONOMOUS:
                    failures.append(f"agent={agent.id}: automatic publish requires autonomous mode")
                if publish_nodes and publish_policy == "approval":
                    approval_nodes = {node_id for node_id, node_type in node_types.items() if node_type == "approval"}
                    for publish_node in publish_nodes:
                        if not (_ancestor_node_ids(edges, publish_node) & approval_nodes):
                            failures.append(
                                f"agent={agent.id}: publish node={publish_node} has approval policy but no approval ancestor"
                            )

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
            if team.kind == AgentTeam.Kind.DEVELOPMENT:
                dev_issues = []
                if not team.project_id:
                    dev_issues.append("development team has no project")
                else:
                    try:
                        binding = team.project.github_repository
                    except Exception:
                        binding = None
                    if binding is None:
                        dev_issues.append("development project has no GitHub repository binding")
                    else:
                        if not binding.installation.active:
                            dev_issues.append("GitHub installation is inactive")
                        if not binding.full_name or not binding.default_branch:
                            dev_issues.append("GitHub repository binding is incomplete")

                enabled_by_role = {item.role: item for item in enabled_members}
                missing_roles = sorted(DEV_REQUIRED_ROLES.difference(enabled_by_role))
                if missing_roles:
                    dev_issues.append("missing required roles: " + ", ".join(missing_roles))

                director_member = enabled_by_role.get("Engineering Director")
                if director_member and director_member.agent_id != team.director_id:
                    dev_issues.append("Engineering Director role does not match team.director")

                architecture = enabled_by_role.get("Architecture")
                if architecture:
                    if not bool(_tool_value(architecture.agent, "github")):
                        dev_issues.append("Architecture role must have GitHub read access")
                    if bool(_tool_value(architecture.agent, "write_code")):
                        dev_issues.append("Architecture role must not write code")

                developer = enabled_by_role.get("Development")
                if developer:
                    if not bool(_tool_value(developer.agent, "github")):
                        dev_issues.append("Development role must have GitHub access")
                    if not bool(_tool_value(developer.agent, "write_code")):
                        dev_issues.append("Development role must allow code changes")
                    if str(_tool_value(developer.agent, "shell") or "").strip().lower() != "sandbox":
                        dev_issues.append("Development role must execute shell only in sandbox")
                    if str(_tool_value(developer.agent, "merge") or "").strip().lower() != "approval":
                        dev_issues.append("Development role must require approval before GitHub write/merge")

                qa = enabled_by_role.get("QA & Security")
                if qa:
                    if str(_tool_value(qa.agent, "shell") or "").strip().lower() != "sandbox":
                        dev_issues.append("QA & Security role must execute checks in sandbox")
                    if bool(_tool_value(qa.agent, "write_code")):
                        dev_issues.append("QA & Security role must be read-only")

                if team.director:
                    if not bool(_tool_value(team.director, "github")):
                        dev_issues.append("Engineering Director must have GitHub access")
                    if not bool(_tool_value(team.director, "approve")):
                        dev_issues.append("Engineering Director must support approval workflow")

                for issue in dev_issues:
                    message = f"team={team.id}: {issue}"
                    if team.active:
                        failures.append(message)
                    else:
                        warnings.append(message + " (team paused)")
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
            if not schedule.skip_if_running:
                warnings.append(
                    f"schedule={schedule.id}: legacy skip_if_running=false is ignored; runtime still forbids overlap"
                )
            if schedule.interval_minutes < 5:
                failures.append(f"schedule={schedule.id}: interval below 5 minutes")
            if schedule.cadence != AgentSchedule.Cadence.INTERVAL and schedule.local_time is None:
                failures.append(f"schedule={schedule.id}: calendar cadence has no local_time")
            if schedule.cadence == AgentSchedule.Cadence.WEEKLY and not list(schedule.weekdays or []):
                failures.append(f"schedule={schedule.id}: weekly cadence has no weekdays")
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

        dev_runs = AgentRun.objects.filter(team__kind=AgentTeam.Kind.DEVELOPMENT).select_related("team", "project")
        for run in dev_runs.filter(state__in=ACTIVE_RUN_STATES)[:500]:
            payload = run.input_payload or {}
            phase = str(payload.get("phase") or "").strip()
            if run.state == AgentRun.State.WAITING_APPROVAL and phase == "awaiting_github_approval":
                pending = run.approvals.filter(
                    status=AgentApproval.Status.PENDING,
                    action_payload__kind="github_changes",
                ).exists()
                if not pending:
                    failures.append(f"run={run.id}: awaiting GitHub approval but no pending approval exists")
            if phase == "reviewing_changes" and not str(payload.get("working_branch") or "").strip():
                failures.append(f"run={run.id}: reviewing_changes has no working_branch")
            if phase == "completed" and run.state != AgentRun.State.COMPLETED:
                failures.append(f"run={run.id}: phase=completed but state={run.state}")

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
