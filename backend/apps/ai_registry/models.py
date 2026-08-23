import uuid

from django.db import models


class Provider(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)


class AIModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="models")
    slug = models.SlugField(unique=True)
    display_name = models.CharField(max_length=120)
    enabled = models.BooleanField(default=True)
    context_window = models.PositiveIntegerField(default=8192)
    max_output_tokens = models.PositiveIntegerField(default=2048)
    input_price_rub_per_million = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    output_price_rub_per_million = models.DecimalField(max_digits=12, decimal_places=4, default=0)
