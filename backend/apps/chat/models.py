import uuid

from django.conf import settings
from django.db import models
from pgvector.django import VectorField


class Conversation(models.Model):
    class RoutingMode(models.TextChoices):
        MANUAL = "manual", "Вручную"
        ECONOMY = "economy", "Эконом"
        BALANCED = "balanced", "Баланс"
        MAXIMUM = "maximum", "Максимум"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations"
    )
    title = models.CharField(max_length=200, default="Новый чат")
    selected_model = models.CharField(max_length=100, default="echo-v1")
    routing_mode = models.CharField(
        max_length=16, choices=RoutingMode.choices, default=RoutingMode.MANUAL
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    memory_enabled = models.BooleanField(default=True)
    active_branch = models.ForeignKey(
        "chat.ConversationBranch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_conversations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ConversationBranch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="branches"
    )
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children"
    )
    forked_from = models.ForeignKey(
        "chat.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forked_branches",
    )
    title = models.CharField(max_length=160, default="Основная ветка")
    inherited_message_ids = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


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
    branch = models.ForeignKey(
        ConversationBranch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="messages",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(blank=True)
    content_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    embedding = VectorField(dimensions=384, null=True, blank=True)
    embedding_model = models.CharField(max_length=80, blank=True)
    indexed_at = models.DateTimeField(null=True, blank=True)
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
        indexes = [models.Index(fields=["role", "created_at"], name="message_role_created_idx")]


class ConversationDraft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="draft"
    )
    content = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)


class ConversationSummary(models.Model):
    """Derived, replaceable summary. Messages remain the source of truth."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="rolling_summary"
    )
    content = models.TextField(blank=True)
    through_message = models.ForeignKey(
        Message,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="summary_checkpoints",
    )
    source_message_count = models.PositiveIntegerField(default=0)
    token_estimate = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
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


class CompareRun(models.Model):
    class State(models.TextChoices):
        PREVIEWED = "previewed", "Ожидает подтверждения"
        RUNNING = "running", "Выполняется"
        COMPLETED = "completed", "Готово"
        PARTIAL = "partial", "Частично"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="compare_runs"
    )
    branch = models.ForeignKey(
        ConversationBranch, on_delete=models.SET_NULL, null=True, related_name="compare_runs"
    )
    source_message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, blank=True, related_name="compare_runs"
    )
    prompt = models.TextField()
    idempotency_key = models.CharField(max_length=160, unique=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PREVIEWED)
    model_slugs = models.JSONField(default=list)
    expected_min_rub = models.DecimalField(max_digits=14, decimal_places=4)
    expected_max_rub = models.DecimalField(max_digits=14, decimal_places=4)
    actual_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    reservation_id = models.UUIDField(null=True, blank=True)
    synthesis_model_slug = models.CharField(max_length=100, blank=True)
    synthesis_output = models.TextField(blank=True)
    synthesis_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    synthesis_reservation_id = models.UUIDField(null=True, blank=True)
    synthesis_pricing_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class CompareVariant(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Выполняется"
        COMPLETED = "completed", "Готово"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    compare_run = models.ForeignKey(CompareRun, on_delete=models.CASCADE, related_name="variants")
    model = models.ForeignKey(
        "ai_registry.AIModel", on_delete=models.PROTECT, related_name="compare_variants"
    )
    position = models.PositiveSmallIntegerField()
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    output = models.TextField(blank=True)
    provider_request_id = models.CharField(max_length=200, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    expected_min_rub = models.DecimalField(max_digits=14, decimal_places=4)
    expected_max_rub = models.DecimalField(max_digits=14, decimal_places=4)
    actual_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    provider_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    pricing_snapshot = models.JSONField(default=dict)
    error_code = models.CharField(max_length=80, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["compare_run", "model"], name="unique_compare_run_model"
            )
        ]


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


class RoutingDecision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generation = models.OneToOneField(
        Generation, on_delete=models.CASCADE, related_name="routing_decision"
    )
    policy = models.ForeignKey(
        "ai_registry.RoutingPolicyVersion", on_delete=models.PROTECT, related_name="decisions"
    )
    mode = models.CharField(max_length=16, choices=Conversation.RoutingMode.choices)
    task_taxonomy = models.CharField(max_length=32)
    classification_confidence = models.DecimalField(max_digits=5, decimal_places=4)
    required_capabilities = models.JSONField(default=list)
    signals = models.JSONField(default=dict)
    selected_model = models.ForeignKey(
        "ai_registry.AIModel", on_delete=models.PROTECT, related_name="routing_selections"
    )
    candidate_snapshot = models.JSONField(default=list)
    explanation = models.TextField()
    estimated_input_tokens = models.PositiveIntegerField()
    estimated_output_tokens = models.PositiveIntegerField()
    estimated_cost_rub = models.DecimalField(max_digits=14, decimal_places=4)
    created_at = models.DateTimeField(auto_now_add=True)
