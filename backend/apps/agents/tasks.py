from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .generic_team_runtime import execute_generic_team_run
from .graph_runtime import execute_graph_run
from .models import Agent, AgentRun, AgentTeam
from .run_views import create_single_agent_run
from .runtime import execute_run
from .team_runtime import execute_team_run

ACTIVE_RUN_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}


@shared_task(bind=True, max_retries=0, soft_time_limit=900, time_limit=930)
def execute_agent_run_task(self, run_id):
    subject = (
        AgentRun.objects.filter(pk=run_id)
        .values("agent_id", "agent__graph", "agent__tool_policy", "team_id", "team__kind", "team__director__role")
        .first()
    )
    if subject is None:
        return {"run_id": str(run_id), "state": "missing"}
    if not subject["team_id"]:
        graph = subject.get("agent__graph") or {}
        tools = subject.get("agent__tool_policy") or {}
        has_graph = bool(graph.get("nodes"))
        # GitHub/code agents remain on the specialized runtime. Ordinary visual
        # employees execute their actual graph node-by-node.
        run = execute_graph_run(run_id) if has_graph and not bool(tools.get("github")) else execute_run(run_id)
    else:
        role = str(subject.get("team__director__role") or "").strip().casefold()
        is_legacy_dev = role == "engineering director"
        if subject["team__kind"] == AgentTeam.Kind.DEVELOPMENT or is_legacy_dev:
            run = execute_team_run(run_id)
        else:
            run = execute_generic_team_run(run_id)
    return {"run_id": str(run.id), "state": run.state}


@shared_task(max_retries=0)
def dispatch_due_agent_schedules(limit=50):
    from .schedule_models import AgentSchedule

    now = timezone.now()
    due_ids = list(
        AgentSchedule.objects.filter(enabled=True, next_run_at__lte=now)
        .order_by("next_run_at")
        .values_list("id", flat=True)[: max(1, min(int(limit), 200))]
    )
    launched = 0
    skipped = 0
    waiting_approval = 0
    for schedule_id in due_ids:
        with transaction.atomic():
            schedule = (
                AgentSchedule.objects.select_for_update()
                .select_related("agent", "team")
                .filter(pk=schedule_id, enabled=True, next_run_at__lte=now)
                .first()
            )
            if schedule is None:
                continue
            schedule.next_run_at = now + timedelta(minutes=max(5, int(schedule.interval_minutes)))
            schedule.last_run_at = now

            if schedule.agent_id:
                subject = Agent.objects.select_for_update().get(pk=schedule.agent_id)
                if subject.status != Agent.Status.ACTIVE:
                    schedule.save(update_fields=["next_run_at", "last_run_at", "updated_at"])
                    skipped += 1
                    continue
                active = AgentRun.objects.filter(agent_id=subject.id, state__in=ACTIVE_RUN_STATES).exists()
                objective = (schedule.objective or subject.objective).strip()
                project = subject.project
            else:
                subject = AgentTeam.objects.select_for_update().get(pk=schedule.team_id)
                if not subject.active:
                    schedule.save(update_fields=["next_run_at", "last_run_at", "updated_at"])
                    skipped += 1
                    continue
                active = AgentRun.objects.filter(team_id=subject.id, state__in=ACTIVE_RUN_STATES).exists()
                objective = (schedule.objective or subject.objective).strip()
                project = subject.project

            if schedule.skip_if_running and active:
                schedule.save(update_fields=["next_run_at", "last_run_at", "updated_at"])
                skipped += 1
                continue
            if not objective:
                schedule.save(update_fields=["next_run_at", "last_run_at", "updated_at"])
                skipped += 1
                continue

            if schedule.agent_id:
                run = create_single_agent_run(
                    owner=schedule.owner,
                    agent=subject,
                    objective=objective,
                    input_payload={"trigger": "schedule", "schedule_id": str(schedule.id)},
                )
                if run.state == AgentRun.State.WAITING_APPROVAL:
                    waiting_approval += 1
                else:
                    launched += 1
            else:
                run = AgentRun.objects.create(
                    owner=schedule.owner,
                    team=subject,
                    project=project,
                    objective=objective,
                    input_payload={"trigger": "schedule", "schedule_id": str(schedule.id)},
                    state=AgentRun.State.QUEUED,
                )
                transaction.on_commit(lambda run_id=str(run.id): execute_agent_run_task.delay(run_id))
                launched += 1
            schedule.last_run = run
            schedule.save(update_fields=["next_run_at", "last_run_at", "last_run", "updated_at"])

    return {
        "checked": len(due_ids),
        "launched": launched,
        "waiting_approval": waiting_approval,
        "skipped": skipped,
    }
