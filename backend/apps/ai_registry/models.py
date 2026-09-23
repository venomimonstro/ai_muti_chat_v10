import base64
import hashlib
import os
import uuid

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def _credential_cipher():
    material = f"ai-workspace-provider-credential:{settings.SECRET_KEY}".encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


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
    adapter_type = models.CharField(max_length=32, choices=AdapterType.choices, default=AdapterType.ECHO)
    api_base_url = models.URLField(blank=True)
    auth_config = models.JSONField(default=dict, blank=True)
    credential_env = models.CharField(max_length=100, blank=True)
    credential_secret = models.TextField(blank=True, editable=False)
    health_state = models.CharField(max_length=16, choices=HealthState.choices, default=HealthState.UNKNOWN)
    consecutive_failures = models.PositiveIntegerField(default=0)
    circuit_opened_until = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_latency_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["priority", "name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hydrate_runtime_credential()

    def set_api_key(self, value: str):
        value = (value or "").strip()
        if not value:
            raise ValidationError("API-ключ не может быть пустым")
        self.credential_secret = _credential_cipher().encrypt(value.encode("utf-8")).decode("ascii")

    def clear_api_key(self):
        self.credential_secret = ""
        if self.credential_env:
            os.environ.pop(self.credential_env, None)

    def _legacy_api_key(self) -> str:
        if not self.credential_secret:
            return ""
        try:
            return _credential_cipher().decrypt(self.credential_secret.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            return ""

    def get_api_key(self) -> str:
        try:
            funding = (
                self.funding_accounts.filter(active=True, is_default=True, api_key__isnull=False, api_key__enabled=True)
                .select_related("api_key")
                .first()
            )
            if funding is not None:
                value = funding.api_key.get_secret()
                if value:
                    return value
        except Exception:
            pass
        try:
            for health_state in ("healthy", "unknown", "degraded"):
                keys = self.api_keys.filter(
                    enabled=True,
                    health_state=health_state,
                ).order_by("priority", "created_at")[:5]
                for key in keys:
                    value = key.get_secret()
                    if value:
                        return value
        except Exception:
            pass
        return self._legacy_api_key() or (os.getenv(self.credential_env, "").strip() if self.credential_env else "")

    def credential_configured(self) -> bool:
        try:
            if self.api_keys.filter(enabled=True).exists():
                return True
        except Exception:
            pass
        return bool(self._legacy_api_key() or (self.credential_env and os.getenv(self.credential_env, "").strip()))

    def credential_source(self) -> str:
        try:
            if self.api_keys.filter(enabled=True).exists():
                return "key_pool"
        except Exception:
            pass
        if self._legacy_api_key():
            return "database"
        if self.credential_env and os.getenv(self.credential_env, "").strip():
            return "environment"
        return "none"

    def _hydrate_runtime_credential(self):
        if not getattr(self, "credential_env", "") or not getattr(self, "credential_secret", ""):
            return
        value = self._legacy_api_key()
        if value:
            os.environ[self.credential_env] = value

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._hydrate_runtime_credential()


class ProviderApiKey(models.Model):
    class HealthState(models.TextChoices):
        UNKNOWN = "unknown", "Не проверен"
        HEALTHY = "healthy", "Работает"
        DEGRADED = "degraded", "Ошибка"
        DISABLED = "disabled", "Отключён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="api_keys")
    label = models.CharField(max_length=120, default="Основной ключ")
    secret_encrypted = models.TextField(editable=False)
    enabled = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)
    health_state = models.CharField(max_length=16, choices=HealthState.choices, default=HealthState.UNKNOWN)
    last_error_code = models.CharField(max_length=80, blank=True)
    last_latency_ms = models.PositiveIntegerField(null=True, blank=True)
    balance_amount = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    balance_currency = models.CharField(max_length=12, blank=True)
    balance_supported = models.BooleanField(default=False)
    balance_checked_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["priority", "created_at"]
        constraints = [models.UniqueConstraint(fields=["provider", "label"], name="unique_provider_api_key_label")]

    def set_secret(self, value: str):
        value = (value or "").strip()
        if not value:
            raise ValidationError("API-ключ не может быть пустым")
        self.secret_encrypted = _credential_cipher().encrypt(value.encode("utf-8")).decode("ascii")

    def get_secret(self) -> str:
        if not self.secret_encrypted:
            return ""
        try:
            return _credential_cipher().decrypt(self.secret_encrypted.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            return ""

    @property
    def masked(self):
        value = self.get_secret()
        if len(value) <= 8:
            return "••••••••"
        return f"{value[:4]}••••{value[-4:]}"


class AIModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="models")
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=120)
    upstream_model = models.CharField(max_length=160, default="")
    enabled = models.BooleanField(default=True)
    capabilities = models.JSONField(default=list, blank=True)
    routing_tags = models.JSONField(default=list, blank=True)
    fallback_model = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="fallback_for")
    context_window = models.PositiveIntegerField(default=8192)
    max_output_tokens = models.PositiveIntegerField(default=2048)
    input_price_rub_per_million = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    output_price_rub_per_million = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    current_version = models.ForeignKey("ModelVersion", on_delete=models.PROTECT, null=True, blank=True, related_name="active_for_models")


class ModelVersion(models.Model):
    class Stage(models.TextChoices):
        CANDIDATE = "candidate", "Кандидат"
        CANARY = "canary", "Canary"
        ACTIVE = "active", "Активна"
        RETIRED = "retired", "Выведена"

    IMMUTABLE_FIELDS = ("model_id", "version", "exact_api_id", "capabilities", "routing_tags", "context_window", "max_output_tokens")
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
            models.UniqueConstraint(fields=["model", "version"], name="unique_model_registry_version"),
            models.UniqueConstraint(fields=["model"], condition=models.Q(stage="active"), name="unique_active_version_per_model"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(*self.IMMUTABLE_FIELDS).first()
            changed = previous and any(previous[field] != getattr(self, field) for field in self.IMMUTABLE_FIELDS)
            if changed:
                raise ValidationError("Конфигурация ModelVersion неизменяема; создайте новую версию")
        super().save(*args, **kwargs)


class ModelVersionTransition(models.Model):
    class Action(models.TextChoices):
        PROMOTE = "promote", "Продвижение"
        ROLLBACK = "rollback", "Откат"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model = models.ForeignKey(AIModel, on_delete=models.PROTECT, related_name="version_transitions")
    from_version = models.ForeignKey(ModelVersion, on_delete=models.PROTECT, null=True, blank=True, related_name="transitions_from")
    to_version = models.ForeignKey(ModelVersion, on_delete=models.PROTECT, related_name="transitions_to")
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
        constraints = [models.UniqueConstraint(fields=["active"], condition=models.Q(active=True), name="unique_active_routing_policy")]
