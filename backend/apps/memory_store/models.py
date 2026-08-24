import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class MemoryItem(models.Model):
    class Scope(models.TextChoices):
        GLOBAL = "global", "Пользователь"
        PROJECT = "project", "Проект"
        CONVERSATION = "conversation", "Чат"

    class Type(models.TextChoices):
        FACT = "fact", "Факт"
        PREFERENCE = "preference", "Предпочтение"
        INSTRUCTION = "instruction", "Инструкция"
        DECISION = "decision", "Решение"

    class Status(models.TextChoices):
        ACTIVE = "active", "Активно"
        ARCHIVED = "archived", "В архиве"
        SUPERSEDED = "superseded", "Заменено"
        DELETED = "deleted", "Удалено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memory_items"
    )
    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="memory_items",
    )
    conversation = models.ForeignKey(
        "chat.Conversation",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="memory_items",
    )
    scope = models.CharField(max_length=20, choices=Scope.choices)
    memory_type = models.CharField(max_length=20, choices=Type.choices, default=Type.FACT)
    content = models.TextField(max_length=4000)
    normalized_content = models.TextField(max_length=4000)
    subject_key = models.CharField(max_length=160, blank=True)
    importance_score = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default="0.50",
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    confidence_score = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default="1.00",
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    trust_level = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default="1.00",
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    source_message = models.ForeignKey(
        "chat.Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="memories"
    )
    source_kind = models.CharField(max_length=24, default="user_explicit")
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    pinned = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-pinned", "-importance_score", "-updated_at"]
        indexes = [
            models.Index(fields=["owner", "scope", "status"]),
            models.Index(fields=["project", "status"]),
            models.Index(fields=["conversation", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(scope="global", project__isnull=True, conversation__isnull=True)
                    | models.Q(scope="project", project__isnull=False, conversation__isnull=True)
                    | models.Q(
                        scope="conversation", project__isnull=True, conversation__isnull=False
                    )
                ),
                name="valid_memory_scope_binding",
            )
        ]

    def clean(self):
        if self.scope == self.Scope.GLOBAL and (self.project_id or self.conversation_id):
            raise ValidationError("Глобальная память не может быть привязана к проекту или чату")
        if self.scope == self.Scope.PROJECT and not self.project_id:
            raise ValidationError("Для памяти проекта необходимо выбрать проект")
        if self.scope == self.Scope.PROJECT and self.conversation_id:
            raise ValidationError("Память проекта не может быть привязана к чату")
        if self.scope == self.Scope.CONVERSATION and not self.conversation_id:
            raise ValidationError("Для памяти чата необходимо выбрать чат")


class MemoryRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(MemoryItem, on_delete=models.CASCADE, related_name="revisions")
    editor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    content = models.TextField(max_length=4000)
    normalized_content = models.TextField(max_length=4000)
    scope = models.CharField(max_length=20, choices=MemoryItem.Scope.choices)
    project_id_snapshot = models.UUIDField(null=True, blank=True)
    conversation_id_snapshot = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class GenerationMemoryUsage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generation = models.ForeignKey(
        "chat.Generation", on_delete=models.CASCADE, related_name="memory_usages"
    )
    memory_item = models.ForeignKey(
        MemoryItem, null=True, on_delete=models.SET_NULL, related_name="generation_usages"
    )
    content_snapshot = models.TextField(max_length=4000)
    scope = models.CharField(max_length=20, choices=MemoryItem.Scope.choices)
    position = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["generation", "position"], name="unique_generation_memory_position"
            )
        ]


class MemoryCandidate(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает подтверждения"
        CONFLICT = "conflict", "Есть конфликт"
        DUPLICATE = "duplicate", "Дубликат"
        ACCEPTED = "accepted", "Принято"
        REJECTED = "rejected", "Отклонено"
        DISMISSED = "dismissed", "Скрыто"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memory_candidates"
    )
    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="memory_candidates",
    )
    conversation = models.ForeignKey(
        "chat.Conversation", on_delete=models.CASCADE, related_name="memory_candidates"
    )
    source_message = models.ForeignKey(
        "chat.Message", on_delete=models.CASCADE, related_name="memory_candidates"
    )
    suggested_scope = models.CharField(max_length=20, choices=MemoryItem.Scope.choices)
    memory_type = models.CharField(max_length=20, choices=MemoryItem.Type.choices)
    content = models.TextField(max_length=1000)
    normalized_content = models.TextField(max_length=1000)
    subject_key = models.CharField(max_length=160)
    confidence_score = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    trust_level = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default="1.00",
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    source_kind = models.CharField(max_length=24, default="direct_user")
    extraction_version = models.CharField(max_length=32, default="rules-v1")
    reason = models.CharField(max_length=240)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    duplicate_of = models.ForeignKey(
        MemoryItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicate_candidates",
    )
    conflicts_with = models.ForeignKey(
        MemoryItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conflicting_candidates",
    )
    accepted_item = models.OneToOneField(
        MemoryItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accepted_candidate",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "status", "created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["source_message", "subject_key"],
                name="unique_memory_candidate_subject_per_message",
            )
        ]
