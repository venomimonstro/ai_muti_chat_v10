from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import AgentApproval, AgentStepRun


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
