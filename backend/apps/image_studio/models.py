import uuid

from django.conf import settings
from django.db import models


def default_image_sizes():
    return ["1024x1024"]


def default_image_qualities():
    return ["standard"]


class ImageModel(models.Model):
    class AdapterType(models.TextChoices):
        ECHO = "echo", "Тестовый"
        OPENAI_IMAGES = "openai_images", "OpenAI Images API"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(
        "ai_registry.Provider", on_delete=models.PROTECT, related_name="image_models"
    )
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=120)
    upstream_model = models.CharField(max_length=160)
    adapter_type = models.CharField(
        max_length=32, choices=AdapterType.choices, default=AdapterType.ECHO
    )
    enabled = models.BooleanField(default=True)
    supported_sizes = models.JSONField(default=default_image_sizes)
    supported_qualities = models.JSONField(default=default_image_qualities)
    max_images = models.PositiveSmallIntegerField(default=4)
    provider_currency = models.CharField(max_length=3, default="RUB")
    provider_price_per_image = models.DecimalField(max_digits=14, decimal_places=6)
    markup_percent = models.DecimalField(max_digits=7, decimal_places=3, default=100)

    class Meta:
        ordering = ["display_name"]


class ImageGeneration(models.Model):
    class State(models.TextChoices):
        RUNNING = "running", "Выполняется"
        COMPLETED = "completed", "Готово"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="image_generations"
    )
    model = models.ForeignKey(ImageModel, on_delete=models.PROTECT, related_name="generations")
    prompt = models.TextField()
    size = models.CharField(max_length=32)
    quality = models.CharField(max_length=32)
    requested_count = models.PositiveSmallIntegerField()
    actual_count = models.PositiveSmallIntegerField(default=0)
    state = models.CharField(max_length=16, choices=State.choices, default=State.RUNNING)
    idempotency_key = models.CharField(max_length=160)
    reservation = models.ForeignKey(
        "billing.BalanceReservation", on_delete=models.PROTECT, null=True, blank=True
    )
    provider_request_id = models.CharField(max_length=200, blank=True)
    price_snapshot = models.JSONField(default=dict)
    estimated_cost_rub = models.DecimalField(max_digits=14, decimal_places=4)
    provider_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    actual_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "idempotency_key"], name="unique_image_generation_idempotency"
            )
        ]
        indexes = [models.Index(fields=["owner", "-created_at"])]


def image_upload_to(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower()
    return f"private/images/{instance.generation.owner_id}/{instance.generation_id}/{instance.position}.{extension}"


class GeneratedImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generation = models.ForeignKey(
        ImageGeneration, on_delete=models.CASCADE, related_name="images"
    )
    position = models.PositiveSmallIntegerField()
    file = models.FileField(upload_to=image_upload_to)
    mime_type = models.CharField(max_length=40)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    revised_prompt = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["generation", "position"], name="unique_generated_image_position"
            )
        ]
