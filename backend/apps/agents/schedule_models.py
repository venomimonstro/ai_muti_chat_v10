import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AgentSchedule(models.Model):
    class Cadence(models.TextChoices):
        INTERVAL = "interval", "Через интервал"
        DAILY = "daily", "Каждый день"
        WEEKDAYS = "weekdays", "По будням"
        WEEKLY = "weekly", "По выбранным дням"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_schedules")
    agent = models.ForeignKey("agents.Agent", on_delete=models.CASCADE, null=True, blank=True, related_name="schedules")
    team = models.ForeignKey("agents.AgentTeam", on_delete=models.CASCADE, null=True, blank=True, related_name="schedules")
    name = models.CharField(max_length=160)
    objective = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    cadence = models.CharField(max_length=16, choices=Cadence.choices, default=Cadence.INTERVAL)
    interval_minutes = models.PositiveIntegerField(default=1440)
    local_time = models.TimeField(null=True, blank=True)
    timezone_name = models.CharField(max_length=64, default="Europe/Moscow")
    weekdays = models.JSONField(default=list, blank=True)
    next_run_at = models.DateTimeField(db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run = models.ForeignKey("agents.AgentRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="triggered_schedules")
    skip_if_running = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "agents"
        ordering = ["next_run_at", "name"]
        indexes = [models.Index(fields=["enabled", "next_run_at"], name="agt_sched_enabled_next_idx")]
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
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError("Неизвестный часовой пояс") from exc
        if self.cadence != self.Cadence.INTERVAL and self.local_time is None:
            raise ValidationError("Для календарного расписания укажите время запуска")
        days = list(self.weekdays or [])
        if any(not isinstance(day, int) or day < 0 or day > 6 for day in days):
            raise ValidationError("Дни недели должны быть числами от 0 (понедельник) до 6 (воскресенье)")
        if self.cadence == self.Cadence.WEEKLY and not days:
            raise ValidationError("Для недельного расписания выберите хотя бы один день")

    def compute_next_run(self, *, after=None):
        after = after or timezone.now()
        if self.cadence == self.Cadence.INTERVAL:
            return after + timedelta(minutes=max(5, int(self.interval_minutes)))

        tz = ZoneInfo(self.timezone_name)
        local_after = after.astimezone(tz)
        run_time = self.local_time
        allowed = set(range(7))
        if self.cadence == self.Cadence.WEEKDAYS:
            allowed = {0, 1, 2, 3, 4}
        elif self.cadence == self.Cadence.WEEKLY:
            allowed = set(int(day) for day in (self.weekdays or []))

        for offset in range(0, 8):
            candidate_date = local_after.date() + timedelta(days=offset)
            if candidate_date.weekday() not in allowed:
                continue
            candidate_local = datetime.combine(candidate_date, run_time, tzinfo=tz)
            if candidate_local > local_after:
                return candidate_local.astimezone(dt_timezone.utc)
        raise ValidationError("Не удалось вычислить следующий запуск")
