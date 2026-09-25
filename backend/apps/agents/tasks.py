from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .generic_team_runtime import execute_generic_team_run
from .graph_runtime import execute_graph_run
from .models import Agent, AgentApproval, AgentRun, AgentTeam
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
        if has_graph and not bool(tools.get("github")):
            run = execute_graph_run(run_id)
            if run.state == AgentRun.State.COMPLETED:
                from .publish_runtime import finalize_graph_publish_nodes

                run = finalize_graph_publish_nodes(run.id)
        else:
            run = execute_run(run_id)
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

            # Advance the cursor before any downstream work so a failed/slow
            # launch cannot make the same due row fire repeatedly every minute.
            schedule.next_run_at = schedule.compute_next_run(after=now)

            if schedule.agent_id:
                subject = Agent.objects.select_for_update().get(pk=schedule.agent_id)
                if subject.status != Agent.Status.ACTIVE:
                    schedule.save(update_fields=["next_run_at", "updated_at"])
                    skipped += 1
                    continue
                active = AgentRun.objects.filter(agent_id=subject.id, state__in=ACTIVE_RUN_STATES).exists()
                objective = (schedule.objective or subject.objective).strip()
                project = subject.project
            else:
                subject = AgentTeam.objects.select_for_update().get(pk=schedule.team_id)
                if not subject.active:
                    schedule.save(update_fields=["next_run_at", "updated_at"])
                    skipped += 1
                    continue
                active = AgentRun.objects.filter(team_id=subject.id, state__in=ACTIVE_RUN_STATES).exists()
                objective = (schedule.objective or subject.objective).strip()
                project = subject.project

            if schedule.skip_if_running and active:
                schedule.save(update_fields=["next_run_at", "updated_at"])
                skipped += 1
                continue
            if not objective:
                schedule.save(update_fields=["next_run_at", "updated_at"])
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

            schedule.last_run_at = now
            schedule.last_run = run
            schedule.save(update_fields=["next_run_at", "last_run_at", "last_run", "updated_at"])

    return {
        "checked": len(due_ids),
        "launched": launched,
        "waiting_approval": waiting_approval,
        "skipped": skipped,
    }


@shared_task(max_retries=0)
def expire_stale_agent_approvals(limit=500):
    """Expire unattended human approvals without performing the protected action.

    WAITING_APPROVAL is intentionally excluded from generic stale-run recovery: a
    human may legitimately take hours to respond. This task gives that state an
    explicit, configurable lifetime instead of leaving runs blocked forever.
    """
    hours = max(1, int(getattr(settings, "AGENT_APPROVAL_TIMEOUT_HOURS", 72)))
    cutoff = timezone.now() - timedelta(hours=hours)
    approval_ids = list(
        AgentApproval.objects.filter(
            status=AgentApproval.Status.PENDING,
            created_at__lt=cutoff,
        )
        .order_by("created_at")
        .values_list("id", flat=True)[: max(1, min(int(limit), 2000))]
    )
    expired = 0
    canceled_runs = 0
    for approval_id in approval_ids:
        with transaction.atomic():
            approval = (
                AgentApproval.objects.select_for_update()
                .select_related("run")
                .filter(
                    pk=approval_id,
                    status=AgentApproval.Status.PENDING,
                    created_at__lt=cutoff,
                )
                .first()
            )
            if approval is None:
                continue
            now = timezone.now()
            approval.status = AgentApproval.Status.EXPIRED
            approval.decided_at = now
            approval.save(update_fields=["status", "decided_at"])
            expired += 1

            run = AgentRun.objects.select_for_update().get(pk=approval.run_id)
            if run.state != AgentRun.State.WAITING_APPROVAL:
                continue
            # If another pending approval still exists, the run remains waiting.
            # Otherwise the protected operation is abandoned and the run becomes
            # terminal. No LLM/tool/provider call is started by this cleanup.
            has_other_pending = run.approvals.filter(status=AgentApproval.Status.PENDING).exists()
            if has_other_pending:
                continue
            run.state = AgentRun.State.CANCELED
            run.error_code = "agent_approval_expired"
            run.error_message = (
                f"Подтверждение не получено в течение {hours} ч. "
                "Защищённое действие не выполнялось. Запуск можно повторить."
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
            canceled_runs += 1

    return {"checked": len(approval_ids), "expired": expired, "canceled_runs": canceled_runs}
