import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Agent(models.Model):
    class Autonomy(models.TextChoices):
        CONTROLLED = "controlled", "Контролируемый"
        SEMI_AUTONOMOUS = "semi_autonomous", "Полуавтономный"
        AUTONOMOUS = "autonomous", "Автономный"

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        ACTIVE = "active", "Активен"
        PAUSED = "paused", "Приостановлен"
        ARCHIVED = "archived", "Архив"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agents")
    project = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="agents")
    name = models.CharField(max_length=160)
    role = models.CharField(max_length=160, blank=True)
    objective = models.TextField(blank=True)
    instructions = models.TextField(blank=True)
    autonomy = models.CharField(max_length=24, choices=Autonomy.choices, default=Autonomy.CONTROLLED)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    system_level = models.CharField(max_length=16, default="balanced")
    tool_policy = models.JSONField(default=dict, blank=True)
    graph = models.JSONField(default=dict, blank=True)
    memory_policy = models.JSONField(default=dict, blank=True)
    max_cost_rub_per_run = models.DecimalField(max_digits=12, decimal_places=4, default=10)
    max_cost_rub_per_day = models.DecimalField(max_digits=12, decimal_places=4, default=100)
    max_cost_rub_per_month = models.DecimalField(max_digits=12, decimal_places=4, default=1500)
    max_steps = models.PositiveIntegerField(default=50)
    max_tool_calls = models.PositiveIntegerField(default=50)
    max_handoffs = models.PositiveIntegerField(default=20)
    max_retries_per_step = models.PositiveIntegerField(default=3)
    max_runtime_seconds = models.PositiveIntegerField(default=3600)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["project", "status"]),
        ]

    def clean(self):
        if self.system_level not in {"economy", "balanced", "maximum"}:
            raise ValidationError("system_level должен быть economy, balanced или maximum")
        if self.max_steps < 1 or self.max_tool_calls < 1 or self.max_runtime_seconds < 1:
            raise ValidationError("Лимиты агента должны быть положительными")


class AgentVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_agent_versions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["agent", "version"], name="unique_agent_version")]


class AgentTeam(models.Model):
    class Kind(models.TextChoices):
        GENERIC = "generic", "Универсальная"
        MARKETING = "marketing", "Маркетинг"
        CONTENT = "content", "Контент"
        SALES = "sales", "Продажи"
        DEVELOPMENT = "development", "Разработка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_teams")
    project = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="agent_teams")
    name = models.CharField(max_length=160)
    objective = models.TextField(blank=True)
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.GENERIC, db_index=True)
    director = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="directed_teams")
    active = models.BooleanField(default=True)
    max_cost_rub_per_run = models.DecimalField(max_digits=12, decimal_places=4, default=50)
    max_handoffs = models.PositiveIntegerField(default=30)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def clean(self):
        if self.director_id and self.owner_id and self.director.owner_id != self.owner_id:
            raise ValidationError("Руководитель команды должен принадлежать владельцу команды")
        if self.kind == self.Kind.DEVELOPMENT and not self.project_id:
            raise ValidationError("Команда разработки должна быть привязана к проекту")


class AgentTeamMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(AgentTeam, on_delete=models.CASCADE, related_name="members")
    agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="team_memberships")
    role = models.CharField(max_length=160)
    priority = models.PositiveIntegerField(default=100)
    can_delegate = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["priority", "role"]
        constraints = [models.UniqueConstraint(fields=["team", "agent"], name="unique_agent_team_member")]

    def clean(self):
        if self.team_id and self.agent_id and self.team.owner_id != self.agent.owner_id:
            raise ValidationError("Агент и команда должны принадлежать одному владельцу")


class AgentRun(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued", "В очереди"
        PLANNING = "planning", "Планирование"
        RUNNING = "running", "Выполняется"
        WAITING_TOOL = "waiting_tool", "Ожидает инструмент"
        WAITING_APPROVAL = "waiting_approval", "Ожидает подтверждение"
        REVIEWING = "reviewing", "Проверка"
        COMPLETED = "completed", "Завершён"
        FAILED = "failed", "Ошибка"
        CANCELED = "canceled", "Отменён"
        BUDGET_EXCEEDED = "budget_exceeded", "Лимит бюджета"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_runs")
    agent = models.ForeignKey(Agent, on_delete=models.PROTECT, null=True, blank=True, related_name="runs")
    team = models.ForeignKey(AgentTeam, on_delete=models.PROTECT, null=True, blank=True, related_name="runs")
    project = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="agent_runs")
    state = models.CharField(max_length=24, choices=State.choices, default=State.QUEUED)
    objective = models.TextField()
    input_payload = models.JSONField(default=dict, blank=True)
    output_payload = models.JSONField(default=dict, blank=True)
    plan = models.JSONField(default=list, blank=True)
    cost_reserved_rub = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    cost_actual_rub = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    step_count = models.PositiveIntegerField(default=0)
    tool_call_count = models.PositiveIntegerField(default=0)
    handoff_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=120, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "state", "created_at"])]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(agent__isnull=False, team__isnull=True) | models.Q(agent__isnull=True, team__isnull=False)),
                name="agent_run_exactly_one_subject",
            )
        ]


class AgentStepRun(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "Ожидает"
        RUNNING = "running", "Выполняется"
        WAITING_APPROVAL = "waiting_approval", "Ожидает подтверждение"
        COMPLETED = "completed", "Готово"
        FAILED = "failed", "Ошибка"
        SKIPPED = "skipped", "Пропущено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="steps")
    agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="step_runs")
    sequence = models.PositiveIntegerField()
    node_id = models.CharField(max_length=120, blank=True)
    title = models.CharField(max_length=240)
    action_type = models.CharField(max_length=80)
    state = models.CharField(max_length=24, choices=State.choices, default=State.PENDING)
    input_payload = models.JSONField(default=dict, blank=True)
    output_payload = models.JSONField(default=dict, blank=True)
    public_log = models.TextField(blank=True)
    cost_rub = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    attempt = models.PositiveIntegerField(default=1)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence", "created_at"]
        constraints = [models.UniqueConstraint(fields=["run", "sequence", "attempt"], name="unique_agent_step_attempt")]


class AgentApproval(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        APPROVED = "approved", "Одобрено"
        REJECTED = "rejected", "Отклонено"
        EXPIRED = "expired", "Истекло"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="approvals")
    step = models.ForeignKey(AgentStepRun, on_delete=models.CASCADE, null=True, blank=True, related_name="approvals")
    requested_by_agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="requested_approvals")
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    action_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="agent_approval_decisions")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class AgentHandoff(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="handoffs")
    from_agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="handoffs_sent")
    to_agent = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name="handoffs_received")
    task = models.TextField()
    context = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]


class AgentArtifact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="artifacts")
    step = models.ForeignKey(AgentStepRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="artifacts")
    kind = models.CharField(max_length=80)
    name = models.CharField(max_length=240)
    reference = models.CharField(max_length=500)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
