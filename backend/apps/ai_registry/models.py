import base64
import hashlib
import os
import uuid

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


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
        YANDEXGPT_CHAT = "yandexgpt_chat", "YandexGPT OpenAI-compatible API"
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

    def _loaded_legacy_api_key(self) -> str:
        secret = self.__dict__.get("credential_secret", "")
        if not secret:
            return ""
        try:
            return _credential_cipher().decrypt(secret.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            return ""

    def _legacy_api_key(self) -> str:
        secret = self.credential_secret
        if not secret:
            return ""
        try:
            return _credential_cipher().decrypt(secret.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            return ""

    def select_api_key(self, *, exclude_key_ids=None):
        """Legacy administrative selector; customer runtime uses dispatch.select_runtime_api_key.

        Keep compatibility for management/recovery code. Customer traffic is fail-closed
        in dispatch.py and can use only a verified HEALTHY credential.
        """
        excluded = {str(item) for item in (exclude_key_ids or []) if item}
        preferred = None
        try:
            funding = (
                self.funding_accounts.filter(
                    active=True,
                    is_default=True,
                    api_key__isnull=False,
                    api_key__enabled=True,
                    api_key__health_state__in=(
                        ProviderApiKey.HealthState.HEALTHY,
                        ProviderApiKey.HealthState.UNKNOWN,
                    ),
                )
                .select_related("api_key")
                .first()
            )
            if funding is not None and str(funding.api_key_id) not in excluded:
                preferred = funding.api_key
        except Exception:
            preferred = None

        try:
            if preferred is not None:
                value = preferred.get_secret()
                if value:
                    ProviderApiKey.objects.filter(pk=preferred.pk).update(last_used_at=timezone.now())
                    return value, preferred.pk

            for health_state in (
                ProviderApiKey.HealthState.HEALTHY,
                ProviderApiKey.HealthState.UNKNOWN,
                ProviderApiKey.HealthState.DEGRADED,
            ):
                keys = self.api_keys.filter(enabled=True, health_state=health_state)
                if excluded:
                    keys = keys.exclude(pk__in=excluded)
                keys = keys.order_by(
                    models.F("last_used_at").asc(nulls_first=True),
                    "priority",
                    "created_at",
                )[:10]
                for key in keys:
                    value = key.get_secret()
                    if value:
                        ProviderApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
                        return value, key.pk
        except Exception:
            pass

        legacy = self._legacy_api_key() or (
            os.getenv(self.credential_env, "").strip() if self.credential_env else ""
        )
        return legacy, None

    def get_api_key(self) -> str:
        return self.select_api_key()[0]

    def credential_configured(self) -> bool:
        try:
            if self.api_keys.filter(enabled=True).exclude(health_state=ProviderApiKey.HealthState.DISABLED).exists():
                return True
        except Exception:
            pass
        return bool(self._legacy_api_key() or (self.credential_env and os.getenv(self.credential_env, "").strip()))

    def credential_source(self) -> str:
        try:
            if self.api_keys.filter(enabled=True).exclude(health_state=ProviderApiKey.HealthState.DISABLED).exists():
                return "key_pool"
        except Exception:
            pass
        if self._legacy_api_key():
            return "database"
        if self.credential_env and os.getenv(self.credential_env, "").strip():
            return "environment"
        return "none"

    def _hydrate_runtime_credential(self):
        credential_env = self.__dict__.get("credential_env", "")
        credential_secret = self.__dict__.get("credential_secret", "")
        if not credential_env or not credential_secret:
            return
        value = self._loaded_legacy_api_key()
        if value:
            os.environ[credential_env] = value

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

    def __str__(self):
        return f"{self.display_name} ({self.provider.name})"


class ModelVersion(models.Model):
    class Stage(models.TextChoices):
        TEST = "test", "Тест"
        CANARY = "canary", "Canary"
        ACTIVE = "active", "Активная"
        DEPRECATED = "deprecated", "Устаревшая"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model = models.ForeignKey(AIModel, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=80)
    upstream_id = models.CharField(max_length=160)
    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.TEST)
    traffic_percent = models.PositiveSmallIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    max_error_rate = models.DecimalField(max_digits=6, decimal_places=5, default="0.05000")
    max_p95_latency_ms = models.PositiveIntegerField(default=15000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["model", "version"], name="unique_model_version")]
