import uuid

from django.db import models


class Provider(models.Model):
    class AdapterType(models.TextChoices):
        ECHO = "echo", "Тестовый"
        OPENAI_RESPONSES = "openai_responses", "OpenAI Responses API"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    adapter_type = models.CharField(
        max_length=32, choices=AdapterType.choices, default=AdapterType.ECHO
    )
    api_base_url = models.URLField(blank=True)
    credential_env = models.CharField(max_length=100, blank=True)


class AIModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="models")
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=120)
    upstream_model = models.CharField(max_length=160, default="")
    enabled = models.BooleanField(default=True)
    context_window = models.PositiveIntegerField(default=8192)
    max_output_tokens = models.PositiveIntegerField(default=2048)
    input_price_rub_per_million = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    output_price_rub_per_million = models.DecimalField(max_digits=12, decimal_places=4, default=0)
