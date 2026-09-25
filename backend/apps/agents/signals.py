from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.accounts.models import Notification

from .models import AgentApproval, AgentRun, AgentStepRun


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
