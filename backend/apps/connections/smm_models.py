import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class SMMContentPlan(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        ACTIVE = "active", "Активен"
        COMPLETED = "completed", "Завершён"
        ARCHIVED = "archived", "Архив"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="smm_content_plans")
    agent = models.ForeignKey("agents.Agent", on_delete=models.PROTECT, related_name="smm_content_plans")
    generation_run = models.ForeignKey("agents.AgentRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="smm_generated_plans")
    connection = models.ForeignKey("connections.ExternalConnection", on_delete=models.PROTECT, related_name="smm_content_plans")
    project = models.ForeignKey("projects.Project", on_delete=models.SET_NULL, null=True, blank=True, related_name="smm_content_plans")
    title = models.CharField(max_length=180, default="Контент-план VK")
    business_context = models.TextField(blank=True)
    goal = models.TextField(blank=True)
    audience = models.TextField(blank=True)
    tone = models.CharField(max_length=120, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    auto_publish = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "connections"
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["owner", "status"]), models.Index(fields=["connection", "period_start"])]

    def clean(self):
        if self.period_end < self.period_start:
            raise ValidationError("Конец контент-плана не может быть раньше начала")
        if self.agent_id and self.agent.owner_id != self.owner_id:
            raise ValidationError("SMM-агент должен принадлежать владельцу плана")
        if self.connection_id:
            if self.connection.owner_id != self.owner_id:
                raise ValidationError("VK-подключение должно принадлежать владельцу плана")
            if self.connection.kind != "vk":
                raise ValidationError("Для SMM VK требуется подключение ВКонтакте")


class SMMContentItem(models.Model):
    class Status(models.TextChoices):
        IDEA = "idea", "Идея"
        DRAFT = "draft", "Черновик"
        APPROVED = "approved", "Одобрен"
        SCHEDULED = "scheduled", "Запланирован"
        PUBLISHING = "publishing", "Публикуется"
        PUBLISHED = "published", "Опубликован"
        FAILED = "failed", "Ошибка"

    class MediaSource(models.TextChoices):
        NONE = "none", "Без изображения"
        GENERATED = "generated", "Сгенерировано AI"
        STOCK = "stock", "Бесплатный фотосток"
        UPLOADED = "uploaded", "Загружено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(SMMContentPlan, on_delete=models.CASCADE, related_name="items")
    title = models.CharField(max_length=220)
    topic = models.CharField(max_length=240, blank=True)
    objective = models.CharField(max_length=240, blank=True)
    content = models.TextField(blank=True)
    cta = models.CharField(max_length=300, blank=True)
    hashtags = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.IDEA, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    media_source = models.CharField(max_length=16, choices=MediaSource.choices, default=MediaSource.NONE)
    media_url = models.URLField(max_length=1000, blank=True)
    media_prompt = models.TextField(blank=True)
    media_attribution = models.CharField(max_length=500, blank=True)
    vk_attachment = models.CharField(max_length=255, blank=True)
    external_post_id = models.CharField(max_length=160, blank=True, db_index=True)
    publish_error = models.CharField(max_length=500, blank=True)
    sort_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "connections"
        ordering = ["scheduled_at", "sort_order", "created_at"]
        indexes = [models.Index(fields=["plan", "status"])]

    def clean(self):
        if self.status in {self.Status.SCHEDULED, self.Status.PUBLISHING, self.Status.PUBLISHED} and not self.content.strip():
            raise ValidationError("Для публикации нужен текст поста")
        if self.status == self.Status.SCHEDULED and self.scheduled_at is None:
            raise ValidationError("Для запланированного поста укажите дату и время")


class SMMPublicationAttempt(models.Model):
    class State(models.TextChoices):
        STARTED = "started", "Запущена"
        COMPLETED = "completed", "Опубликована"
        FAILED = "failed", "Ошибка"
        SKIPPED = "skipped", "Пропущена"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(SMMContentItem, on_delete=models.CASCADE, related_name="publication_attempts")
    idempotency_key = models.CharField(max_length=180, unique=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.STARTED)
    external_post_id = models.CharField(max_length=160, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "connections"
        ordering = ["-started_at"]
