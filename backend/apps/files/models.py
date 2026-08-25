import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from pgvector.django import VectorField


def isolated_upload_path(instance, _filename):
    suffix = Path(instance.original_name).suffix.lower()[:12]
    return f"users/{instance.owner_id}/projects/{instance.project_id}/files/{instance.id}{suffix}"


class FileAsset(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Загружен"
        QUARANTINE = "quarantine", "Проверяется"
        PARSING = "parsing", "Извлекается текст"
        READY = "ready", "Готов"
        PARTIAL = "partial", "Частично обработан"
        REJECTED = "rejected", "Отклонён"
        FAILED = "failed", "Ошибка"
        DELETING = "deleting", "Удаляется"
        DELETED = "deleted", "Удалён"

    class ScanStatus(models.TextChoices):
        BASIC_PASSED = "basic_passed", "Базовая проверка пройдена"
        REJECTED = "rejected", "Отклонён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="file_assets"
    )
    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="files")
    blob = models.FileField(upload_to=isolated_upload_path, max_length=500)
    original_name = models.CharField(max_length=255)
    declared_content_type = models.CharField(max_length=160, blank=True)
    detected_type = models.CharField(max_length=32)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED)
    scan_status = models.CharField(max_length=32, choices=ScanStatus.choices)
    error_code = models.CharField(max_length=80, blank=True)
    idempotency_key = models.CharField(max_length=160)
    extracted_chars = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "idempotency_key"], name="unique_file_upload_idempotency"
            )
        ]
        indexes = [models.Index(fields=["owner", "project", "status"])]


class FileProcessingJob(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Выполняется"
        COMPLETED = "completed", "Готово"
        PARTIAL = "partial", "Частично"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(FileAsset, on_delete=models.CASCADE, related_name="jobs")
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    attempt = models.PositiveIntegerField(default=1)
    error_code = models.CharField(max_length=80, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class FileChunk(models.Model):
    class InjectionRisk(models.TextChoices):
        SAFE = "safe", "Безопасный"
        SUSPICIOUS = "suspicious", "Подозрительный"
        BLOCKED = "blocked", "Заблокирован"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(FileAsset, on_delete=models.CASCADE, related_name="chunks")
    position = models.PositiveIntegerField()
    source_location = models.JSONField(default=dict, blank=True)
    content = models.TextField()
    untrusted_content = models.BooleanField(default=True, editable=False)
    content_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    embedding = VectorField(dimensions=384, null=True, blank=True)
    embedding_model = models.CharField(max_length=80, blank=True)
    acl_owner_id = models.UUIDField(null=True, editable=False, db_index=True)
    acl_project_id = models.UUIDField(null=True, editable=False, db_index=True)
    injection_risk = models.CharField(
        max_length=16, choices=InjectionRisk.choices, default=InjectionRisk.SAFE, db_index=True
    )
    injection_signals = models.JSONField(default=list, blank=True)
    indexed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["file", "position"], name="unique_file_chunk_position")
        ]
        indexes = [
            models.Index(fields=["acl_project_id", "injection_risk"], name="filechunk_acl_risk_idx")
        ]
