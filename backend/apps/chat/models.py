import uuid

from django.conf import settings
from django.db import models


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations"
    )
    title = models.CharField(max_length=200, default="Новый чат")
    selected_model = models.CharField(max_length=100, default="echo-v1")
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    memory_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Пользователь"
        ASSISTANT = "assistant", "Ассистент"
        SYSTEM = "system", "Система"

    class Status(models.TextChoices):
        SAVED = "saved", "Сохранено"
        STREAMING = "streaming", "Генерируется"
        COMPLETED = "completed", "Готово"
        PARTIAL = "partial", "Частично"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SAVED)
    client_message_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "client_message_id"],
                condition=models.Q(client_message_id__isnull=False),
                name="unique_client_message_per_conversation",
            )
        ]


class ConversationDraft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="draft"
    )
    content = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)


class Generation(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Выполняется"
        COMPLETED = "completed", "Готово"
        FAILED = "failed", "Ошибка"
        CANCELLED = "cancelled", "Остановлено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_message = models.OneToOneField(
        Message, on_delete=models.PROTECT, related_name="generation_request"
    )
    assistant_message = models.OneToOneField(
        Message, on_delete=models.PROTECT, related_name="generation_response"
    )
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    model = models.CharField(max_length=100)
    idempotency_key = models.CharField(max_length=160, unique=True)
    reservation_id = models.UUIDField(null=True)
    provider_request_id = models.CharField(max_length=200, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    actual_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    error_code = models.CharField(max_length=80, blank=True)
    routed_model = models.CharField(max_length=100, blank=True)
    provider_slug = models.CharField(max_length=100, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    route_price_snapshot = models.JSONField(default=dict, blank=True)
    context_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)


class GenerationAttempt(models.Model):
    class State(models.TextChoices):
        RUNNING = "running", "Выполняется"
        COMPLETED = "completed", "Готово"
        FAILED = "failed", "Ошибка"
        SKIPPED = "skipped", "Пропущено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generation = models.ForeignKey(Generation, on_delete=models.CASCADE, related_name="attempts")
    provider = models.ForeignKey(
        "ai_registry.Provider", on_delete=models.PROTECT, related_name="generation_attempts"
    )
    model_slug = models.CharField(max_length=100)
    sequence = models.PositiveIntegerField()
    state = models.CharField(max_length=16, choices=State.choices, default=State.RUNNING)
    error_code = models.CharField(max_length=80, blank=True)
    retryable = models.BooleanField(default=False)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["generation", "sequence"], name="unique_generation_attempt_sequence"
            )
        ]
