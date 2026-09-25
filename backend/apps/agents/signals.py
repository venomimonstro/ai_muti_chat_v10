from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.accounts.models import Notification

from .models import AgentApproval, AgentHandoff, AgentRun, AgentStepRun


@receiver(post_save, sender=AgentApproval)
def sync_approval_step_state(sender, instance, **kwargs):
    if not instance.step_id:
        return
    if instance.status == AgentApproval.Status.APPROVED:
        AgentStepRun.objects.filter(
            pk=instance.step_id,
            state=AgentStepRun.State.WAITING_APPROVAL,
        ).update(
            state=AgentStepRun.State.COMPLETED,
            public_log="Пользователь подтвердил продолжение workflow.",
            finished_at=instance.decided_at or timezone.now(),
        )
    elif instance.status in {AgentApproval.Status.REJECTED, AgentApproval.Status.EXPIRED}:
        AgentStepRun.objects.filter(
            pk=instance.step_id,
            state=AgentStepRun.State.WAITING_APPROVAL,
        ).update(
            state=AgentStepRun.State.SKIPPED,
            public_log="Действие не выполнено: подтверждение отклонено или истекло.",
            finished_at=instance.decided_at or timezone.now(),
        )


@receiver(post_save, sender=AgentStepRun)
def persist_generic_team_handoff(sender, instance, **kwargs):
    """Record actual role-to-role transfers without another LLM/tool call."""
    if instance.state != AgentStepRun.State.COMPLETED or not str(instance.node_id or "").startswith("team-member-"):
        return
    run = AgentRun.objects.filter(pk=instance.run_id, team__isnull=False).first()
    if run is None or instance.sequence <= 1:
        return
    previous = (
        AgentStepRun.objects.filter(
            run_id=instance.run_id,
            state=AgentStepRun.State.COMPLETED,
            sequence__lt=instance.sequence,
            node_id__startswith="team-member-",
        )
        .select_related("agent")
        .order_by("-sequence", "-created_at")
        .first()
    )
    if previous is None or previous.agent_id == instance.agent_id:
        return
    context_text = str((previous.output_payload or {}).get("text") or previous.public_log or "").strip()[:8000]
    result_text = str((instance.output_payload or {}).get("text") or instance.public_log or "").strip()[:8000]
    task = f"{instance.node_id}: передать этап роли «{instance.title}»"
    handoff, created = AgentHandoff.objects.get_or_create(
        run_id=instance.run_id,
        from_agent_id=previous.agent_id,
        to_agent_id=instance.agent_id,
        task=task,
        defaults={
            "context": {"text": context_text, "from_step_id": str(previous.id)},
            "result": {"text": result_text, "to_step_id": str(instance.id)},
            "completed_at": instance.finished_at or timezone.now(),
        },
    )
    if not created and handoff.completed_at is None:
        handoff.result = {"text": result_text, "to_step_id": str(instance.id)}
        handoff.completed_at = instance.finished_at or timezone.now()
        handoff.save(update_fields=["result", "completed_at"])


def _scheduled_trigger(run):
    return str((run.input_payload or {}).get("trigger") or "") in {"schedule", "schedule_run_now"}


def _subject_name(run):
    if run.agent_id:
        return run.agent.name
    if run.team_id:
        return run.team.name
    return "AI-сотрудник"


@receiver(post_save, sender=AgentRun)
def notify_autonomous_run_state(sender, instance, **kwargs):
    if not _scheduled_trigger(instance):
        return

    title = ""
    body = ""
    level = Notification.Level.INFO
    if instance.state == AgentRun.State.WAITING_APPROVAL:
        title = "AI-сотрудник ждёт подтверждения"
        body = f"{_subject_name(instance)} подготовил следующий шаг и ждёт вашего решения."
        level = Notification.Level.WARNING
    elif instance.state == AgentRun.State.COMPLETED:
        title = "Автономная задача выполнена"
        body = f"{_subject_name(instance)} завершил задачу. Откройте журнал, чтобы посмотреть результат."
        level = Notification.Level.SUCCESS
    elif instance.state in {AgentRun.State.FAILED, AgentRun.State.BUDGET_EXCEEDED}:
        title = "Автономная задача остановлена"
        if instance.state == AgentRun.State.BUDGET_EXCEEDED:
            body = f"{_subject_name(instance)} остановлен до превышения заданного бюджета."
        else:
            body = f"{_subject_name(instance)} не завершил задачу. В журнале сохранена причина ошибки."
        level = Notification.Level.WARNING
    elif instance.state == AgentRun.State.CANCELED and instance.error_code == "agent_approval_expired":
        title = "Подтверждение автономной задачи истекло"
        body = f"{_subject_name(instance)} не выполнил действие без вашего подтверждения. Запуск безопасно завершён."
        level = Notification.Level.WARNING
    else:
        return

    Notification.objects.get_or_create(
        user=instance.owner,
        dedupe_key=f"agent-run:{instance.id}:{instance.state}",
        defaults={
            "title": title,
            "body": body,
            "level": level,
            "action_url": f"/app/runs/{instance.id}",
        },
    )
