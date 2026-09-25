from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import AgentRun, AgentStepRun

WAIT_KEY = "_agent_wait"
MIN_WAIT_MINUTES = 1
MAX_WAIT_MINUTES = 7 * 24 * 60


def wait_metadata(run):
    payload = run.input_payload if isinstance(run.input_payload, dict) else {}
    value = payload.get(WAIT_KEY)
    return value if isinstance(value, dict) else None


def wait_is_due(run, *, now=None):
    meta = wait_metadata(run)
    if not meta:
        return False
    raw = str(meta.get("resume_at") or "").strip()
    resume_at = parse_datetime(raw)
    if resume_at is None:
        return False
    if timezone.is_naive(resume_at):
        resume_at = timezone.make_aware(resume_at, timezone.get_current_timezone())
    return resume_at <= (now or timezone.now())


def handle_wait_node(run, agent, node, sequence):
    """Pause a graph run durably or complete an already elapsed wait."""
    node_id = str(node.get("id") or f"wait-{sequence}")
    title = str(node.get("title") or "Подождать")[:240]
    now = timezone.now()
    existing = run.steps.filter(node_id=node_id).order_by("-created_at").first()
    meta = wait_metadata(run)

    if meta and str(meta.get("node_id") or "") == node_id:
        if not wait_is_due(run, now=now):
            if run.state != AgentRun.State.WAITING_TOOL:
                run.state = AgentRun.State.WAITING_TOOL
                run.save(update_fields=["state", "updated_at"])
            return True

        if existing is not None and existing.state != AgentStepRun.State.COMPLETED:
            existing.state = AgentStepRun.State.COMPLETED
            existing.public_log = "Ожидание завершено. Workflow продолжен автоматически."
            existing.finished_at = now
            existing.save(update_fields=["state", "public_log", "finished_at"])
        payload = dict(run.input_payload or {})
        payload.pop(WAIT_KEY, None)
        run.input_payload = payload
        run.state = AgentRun.State.PLANNING
        # Waiting is not active worker execution. Start a fresh active-time
        # segment so a legitimate multi-hour pause cannot trigger runtime timeout.
        run.started_at = now
        run.step_count = max(run.step_count, sequence)
        run.save(update_fields=["input_payload", "state", "started_at", "step_count", "updated_at"])
        return False

    try:
        minutes = int(node.get("wait_minutes") or 60)
    except (TypeError, ValueError):
        minutes = 60
    minutes = max(MIN_WAIT_MINUTES, min(minutes, MAX_WAIT_MINUTES))
    resume_at = now + timedelta(minutes=minutes)

    if existing is None:
        AgentStepRun.objects.create(
            run=run,
            agent=agent,
            sequence=sequence,
            node_id=node_id,
            title=title,
            action_type="wait",
            state=AgentStepRun.State.PENDING,
            input_payload={"wait_minutes": minutes},
            output_payload={"resume_at": resume_at.isoformat()},
            public_log=f"Workflow поставлен на паузу на {minutes} мин.",
            started_at=now,
        )

    payload = dict(run.input_payload or {})
    payload[WAIT_KEY] = {
        "node_id": node_id,
        "resume_at": resume_at.isoformat(),
        "wait_minutes": minutes,
    }
    run.input_payload = payload
    run.state = AgentRun.State.WAITING_TOOL
    run.step_count = max(run.step_count, sequence)
    run.save(update_fields=["input_payload", "state", "step_count", "updated_at"])
    return True


def resume_due_waits(*, limit=200):
    """Requeue waits that reached resume_at without keeping a worker occupied."""
    now = timezone.now()
    candidate_ids = list(
        AgentRun.objects.filter(state=AgentRun.State.WAITING_TOOL)
        .order_by("updated_at")
        .values_list("id", flat=True)[: max(1, min(int(limit), 1000))]
    )
    resumed = 0
    for run_id in candidate_ids:
        with transaction.atomic():
            run = AgentRun.objects.select_for_update().filter(pk=run_id, state=AgentRun.State.WAITING_TOOL).first()
            if run is None or not wait_metadata(run) or not wait_is_due(run, now=now):
                continue
            run.state = AgentRun.State.QUEUED
            run.save(update_fields=["state", "updated_at"])
            from .tasks import enqueue_agent_run

            transaction.on_commit(lambda current_id=str(run.id): enqueue_agent_run(current_id))
            resumed += 1
    return resumed
