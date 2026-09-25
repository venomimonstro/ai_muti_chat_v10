import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AgentSchedule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_schedules")
    agent = models.ForeignKey("agents.Agent", on_delete=models.CASCADE, null=True, blank=True, related_name="schedules")
    team = models.ForeignKey("agents.AgentTeam", on_delete=models.CASCADE, null=True, blank=True, related_name="schedules")
    name = models.CharField(max_length=160)
    objective = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    interval_minutes = models.PositiveIntegerField(default=1440)
    next_run_at = models.DateTimeField(db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run = models.ForeignKey("agents.AgentRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="triggered_schedules")
    skip_if_running = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "agents"
        ordering = ["next_run_at", "name"]
        indexes = [models.Index(fields=["enabled", "next_run_at"], name="agents_agent_enabled_290322_idx")]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(agent__isnull=False, team__isnull=True) | models.Q(agent__isnull=True, team__isnull=False)),
                name="agent_schedule_exactly_one_subject",
            ),
        ]

    def clean(self):
        if self.interval_minutes < 5:
            raise ValidationError("Минимальный интервал автономного запуска — 5 минут")
        subject = self.agent or self.team
        if subject is not None and self.owner_id and subject.owner_id != self.owner_id:
            raise ValidationError("Расписание и агент/команда должны принадлежать одному пользователю")
