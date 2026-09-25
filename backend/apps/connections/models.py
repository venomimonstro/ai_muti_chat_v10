import base64
import hashlib
import uuid

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def _cipher():
    material = f"ai-workspace-external-connection:{settings.SECRET_KEY}".encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


class ExternalConnection(models.Model):
    class Kind(models.TextChoices):
        WORDPRESS = "wordpress", "WordPress"

    class Health(models.TextChoices):
        UNKNOWN = "unknown", "Не проверено"
        HEALTHY = "healthy", "Работает"
        DEGRADED = "degraded", "Ошибка"
        DISABLED = "disabled", "Отключено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="external_connections")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    name = models.CharField(max_length=160)
    base_url = models.URLField(max_length=500)
    username = models.CharField(max_length=255, blank=True)
    secret_encrypted = models.TextField(editable=False, blank=True)
    enabled = models.BooleanField(default=True)
    health_state = models.CharField(max_length=16, choices=Health.choices, default=Health.UNKNOWN)
    last_error = models.CharField(max_length=240, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "kind", "name"], name="unique_owner_connection_name")
        ]

    def set_secret(self, value):
        value = str(value or "").strip()
        if not value:
            raise ValidationError("Секрет подключения не может быть пустым")
        self.secret_encrypted = _cipher().encrypt(value.encode("utf-8")).decode("ascii")

    def get_secret(self):
        if not self.secret_encrypted:
            return ""
        try:
            return _cipher().decrypt(self.secret_encrypted.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            return ""

    @property
    def masked_secret(self):
        return "••••••••" if self.secret_encrypted else ""


class AgentConnectionBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey("agents.Agent", on_delete=models.CASCADE, related_name="connection_bindings")
    connection = models.ForeignKey(ExternalConnection, on_delete=models.CASCADE, related_name="agent_bindings")
    purpose = models.CharField(max_length=80, default="publish")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["purpose", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["agent", "connection", "purpose"], name="unique_agent_connection_purpose")
        ]

    def clean(self):
        if self.agent_id and self.connection_id and self.agent.owner_id != self.connection.owner_id:
            raise ValidationError("Агент и подключение должны принадлежать одному пользователю")
