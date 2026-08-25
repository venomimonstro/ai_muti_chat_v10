import uuid

from django.core.exceptions import ValidationError
from django.db import models


class Provider(models.Model):
    class AdapterType(models.TextChoices):
        ECHO = "echo", "Тестовый"
        OPENAI_RESPONSES = "openai_responses", "OpenAI Responses API"
        ANTHROPIC_MESSAGES = "anthropic_messages", "Anthropic Messages API"
        DEEPSEEK_CHAT = "deepseek_chat", "DeepSeek Chat API"
        GEMINI_GENERATE_CONTENT = "gemini_generate_content", "Google Gemini API"
        XAI_CHAT = "xai_chat", "xAI Chat Completions API"

    class HealthState(models.TextChoices):
        UNKNOWN = "unknown", "Не проверен"
        HEALTHY = "healthy", "Работает"
        DEGRADED = "degraded", "Нестабилен"
        OPEN = "open", "Circuit открыт"
        DISABLED = "disabled", "Отключён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    emergency_disabled = models.BooleanField(default=False)
    priority = models.PositiveIntegerField(default=100)
    region = models.CharField(max_length=64, blank=True)
    adapter_type = models.CharField(
        max_length=32, choices=AdapterType.choices, default=AdapterType.ECHO
    )
    api_base_url = models.URLField(blank=True)
    credential_env = models.CharField(max_length=100, blank=True)
    health_state = models.CharField(
        max_length=16, choices=HealthState.choices, default=HealthState.UNKNOWN
    )
    consecutive_failures = models.PositiveIntegerField(default=0)
    circuit_opened_until = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_latency_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["priority", "name"]


class AIModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="models")
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=120)
    upstream_model = models.CharField(max_length=160, default="")
    enabled = models.BooleanField(default=True)
    capabilities = models.JSONField(default=list, blank=True)
    routing_tags = models.JSONField(default=list, blank=True)
    fallback_model = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="fallback_for"
    )
    context_window = models.PositiveIntegerField(default=8192)
    max_output_tokens = models.PositiveIntegerField(default=2048)
    input_price_rub_per_million = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    output_price_rub_per_million = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    current_version = models.ForeignKey(
        "ModelVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="active_for_models",
    )


class ModelVersion(models.Model):
    class Stage(models.TextChoices):
        CANDIDATE = "candidate", "Кандидат"
        CANARY = "canary", "Canary"
        ACTIVE = "active", "Активна"
        RETIRED = "retired", "Выведена"

    IMMUTABLE_FIELDS = (
        "model_id",
        "version",
        "exact_api_id",
        "capabilities",
        "routing_tags",
        "context_window",
        "max_output_tokens",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model = models.ForeignKey(AIModel, on_delete=models.PROTECT, related_name="versions")
    version = models.SlugField(max_length=100)
    exact_api_id = models.CharField(max_length=160)
    capabilities = models.JSONField(default=list, blank=True)
    routing_tags = models.JSONField(default=list, blank=True)
    context_window = models.PositiveIntegerField(default=8192)
    max_output_tokens = models.PositiveIntegerField(default=2048)
    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.CANDIDATE)
    release_notes = models.TextField(blank=True)
    eval_run_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["model__slug", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["model", "version"], name="unique_model_registry_version"
            ),
            models.UniqueConstraint(
                fields=["model"],
                condition=models.Q(stage="active"),
                name="unique_active_version_per_model",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(*self.IMMUTABLE_FIELDS).first()
            changed = previous and any(
                previous[field] != getattr(self, field) for field in self.IMMUTABLE_FIELDS
            )
            if changed:
                raise ValidationError("Конфигурация ModelVersion неизменяема; создайте новую версию")
        super().save(*args, **kwargs)


class ModelVersionTransition(models.Model):
    class Action(models.TextChoices):
        PROMOTE = "promote", "Продвижение"
        ROLLBACK = "rollback", "Откат"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model = models.ForeignKey(AIModel, on_delete=models.PROTECT, related_name="version_transitions")
    from_version = models.ForeignKey(
        ModelVersion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transitions_from",
    )
    to_version = models.ForeignKey(
        ModelVersion, on_delete=models.PROTECT, related_name="transitions_to"
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    eval_run_id = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ProviderHealthSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="health_snapshots")
    healthy = models.BooleanField()
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-checked_at"]
        indexes = [models.Index(fields=["provider", "-checked_at"])]


class ReliabilityIncident(models.Model):
    class State(models.TextChoices):
        OPEN = "open", "Открыт"
        RECOVERED = "recovered", "Восстановлен"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="incidents")
    state = models.CharField(max_length=16, choices=State.choices, default=State.OPEN)
    error_code = models.CharField(max_length=80)
    details = models.JSONField(default=dict, blank=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    recovered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]


class RoutingPolicyVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.SlugField(max_length=80, unique=True)
    active = models.BooleanField(default=False)
    mode_weights = models.JSONField(default=dict)
    thresholds = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["active"],
                condition=models.Q(active=True),
                name="unique_active_routing_policy",
            )
        ]
