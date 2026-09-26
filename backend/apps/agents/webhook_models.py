import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AgentWebhookTrigger(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_webhook_triggers")
    agent = models.ForeignKey("agents.Agent", on_delete=models.CASCADE, null=True, blank=True, related_name="webhook_triggers")
    team = models.ForeignKey("agents.AgentTeam", on_delete=models.CASCADE, null=True, blank=True, related_name="webhook_triggers")
    name = models.CharField(max_length=160)
    objective = models.TextField(blank=True)
    secret_hash = models.CharField(max_length=256)
    enabled = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "agents"
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["owner", "enabled"], name="agt_wh_owner_enabled_idx")]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(agent__isnull=False, team__isnull=True) | models.Q(agent__isnull=True, team__isnull=False)),
                name="agent_webhook_exactly_one_subject",
            )
        ]

    def clean(self):
        subject = self.agent or self.team
        if subject is not None and self.owner_id and subject.owner_id != self.owner_id:
            raise ValidationError("Webhook и агент/команда должны принадлежать одному пользователю")


class AgentWebhookDelivery(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "Ожидает"
        QUEUED = "queued", "Передано в очередь"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trigger = models.ForeignKey(AgentWebhookTrigger, on_delete=models.CASCADE, related_name="deliveries")
    event_id = models.CharField(max_length=160)
    payload = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    run = models.ForeignKey("agents.AgentRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="webhook_deliveries")
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "agents"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["trigger", "event_id"], name="unique_agent_webhook_event")
        ]
        indexes = [models.Index(fields=["state", "created_at"], name="agt_wh_state_created_idx")]
