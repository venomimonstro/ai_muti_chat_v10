import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class FeatureFlag(models.Model):
    key = models.SlugField(max_length=100, primary_key=True)
    description = models.CharField(max_length=300, blank=True)
    enabled = models.BooleanField(default=False)
    rollout_percent = models.PositiveSmallIntegerField(default=0)
    allow_user_ids = models.JSONField(default=list, blank=True)
    deny_user_ids = models.JSONField(default=list, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="updated_feature_flags",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def clean(self):
        if not 0 <= self.rollout_percent <= 100:
            raise ValidationError("Rollout percent must be between 0 and 100")


class SecurityEvent(models.Model):
    class Severity(models.TextChoices):
        INFO = "info", "Информация"
        WARNING = "warning", "Предупреждение"
        CRITICAL = "critical", "Критический"

    class Status(models.TextChoices):
        OPEN = "open", "Открыт"
        INVESTIGATING = "investigating", "Расследуется"
        RESOLVED = "resolved", "Закрыт"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.CharField(max_length=80)
    severity = models.CharField(
        max_length=16, choices=Severity.choices, default=Severity.WARNING
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="security_events",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    summary = models.CharField(max_length=240)
    details = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reported_security_events",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="resolved_security_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ReleaseRecord(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Черновик"
        CANARY = "canary", "Canary"
        ROLLING = "rolling", "Поэтапный"
        STABLE = "stable", "Для всех"
        ROLLED_BACK = "rolled_back", "Откатан"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.CharField(max_length=80, unique=True)
    commit_sha = models.CharField(max_length=64)
    environment = models.CharField(max_length=40, default="production")
    state = models.CharField(max_length=20, choices=State.choices, default=State.DRAFT)
    rollout_percent = models.PositiveSmallIntegerField(default=0)
    allow_user_ids = models.JSONField(default=list, blank=True)
    health_snapshot = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_releases",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        if not 0 <= self.rollout_percent <= 100:
            raise ValidationError("Rollout percent must be between 0 and 100")


class BackupRecord(models.Model):
    class Kind(models.TextChoices):
        DATABASE = "database", "База данных"
        MEDIA = "media", "Медиа"
        FULL = "full", "Полный"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Запрошен"
        RUNNING = "running", "Выполняется"
        SUCCEEDED = "succeeded", "Создан"
        FAILED = "failed", "Ошибка"
        VERIFIED = "verified", "Проверен"
        RESTORED = "restored", "Тест восстановления пройден"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.REQUESTED
    )
    storage_reference = models.CharField(max_length=300, blank=True)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_backups",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    restored_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class AdminAuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="admin_audit_events",
    )
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=80)
    target_id = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Admin audit events are immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Admin audit events cannot be deleted")


class ComplianceSignoff(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        APPROVED = "approved", "Подтверждено"
        BLOCKED = "blocked", "Блокирует запуск"

    key = models.SlugField(max_length=100, primary_key=True)
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    evidence_reference = models.CharField(max_length=400, blank=True)
    notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compliance_signoffs",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]


class StatusIncident(models.Model):
    class Impact(models.TextChoices):
        MINOR = "minor", "Незначительный"
        MAJOR = "major", "Серьёзный"
        CRITICAL = "critical", "Критический"

    class State(models.TextChoices):
        INVESTIGATING = "investigating", "Расследуется"
        MONITORING = "monitoring", "Наблюдение"
        RESOLVED = "resolved", "Устранён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    message = models.TextField()
    impact = models.CharField(max_length=16, choices=Impact.choices)
    state = models.CharField(
        max_length=20, choices=State.choices, default=State.INVESTIGATING
    )
    affected_components = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_status_incidents",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
