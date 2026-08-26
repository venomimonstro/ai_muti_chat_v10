import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models

import apps.image_studio.models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("ai_registry", "0005_modelversion_and_provider_families"),
        ("billing", "0004_cost_protection"),
    ]
    operations = [
        migrations.CreateModel(
            name="ImageModel",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("slug", models.SlugField(unique=True)),
                ("display_name", models.CharField(max_length=120)),
                ("upstream_model", models.CharField(max_length=160)),
                ("adapter_type", models.CharField(choices=[("echo", "Тестовый"), ("openai_images", "OpenAI Images API")], default="echo", max_length=32)),
                ("enabled", models.BooleanField(default=True)),
                ("supported_sizes", models.JSONField(default=apps.image_studio.models.default_image_sizes)),
                ("supported_qualities", models.JSONField(default=apps.image_studio.models.default_image_qualities)),
                ("max_images", models.PositiveSmallIntegerField(default=4)),
                ("provider_currency", models.CharField(default="RUB", max_length=3)),
                ("provider_price_per_image", models.DecimalField(decimal_places=6, max_digits=14)),
                ("markup_percent", models.DecimalField(decimal_places=3, default=100, max_digits=7)),
                ("provider", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="image_models", to="ai_registry.provider")),
            ],
            options={"ordering": ["display_name"]},
        ),
        migrations.CreateModel(
            name="ImageGeneration",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("prompt", models.TextField()), ("size", models.CharField(max_length=32)),
                ("quality", models.CharField(max_length=32)), ("requested_count", models.PositiveSmallIntegerField()),
                ("actual_count", models.PositiveSmallIntegerField(default=0)),
                ("state", models.CharField(choices=[("running", "Выполняется"), ("completed", "Готово"), ("failed", "Ошибка")], default="running", max_length=16)),
                ("idempotency_key", models.CharField(max_length=160)),
                ("provider_request_id", models.CharField(blank=True, max_length=200)),
                ("price_snapshot", models.JSONField(default=dict)),
                ("estimated_cost_rub", models.DecimalField(decimal_places=4, max_digits=14)),
                ("provider_cost_rub", models.DecimalField(decimal_places=4, max_digits=14, null=True)),
                ("actual_cost_rub", models.DecimalField(decimal_places=4, max_digits=14, null=True)),
                ("error_code", models.CharField(blank=True, max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("model", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="generations", to="image_studio.imagemodel")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="image_generations", to=settings.AUTH_USER_MODEL)),
                ("reservation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="billing.balancereservation")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="GeneratedImage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("position", models.PositiveSmallIntegerField()),
                ("file", models.FileField(upload_to=apps.image_studio.models.image_upload_to)),
                ("mime_type", models.CharField(max_length=40)), ("size_bytes", models.PositiveIntegerField()),
                ("sha256", models.CharField(max_length=64)), ("revised_prompt", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("generation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="images", to="image_studio.imagegeneration")),
            ],
            options={"ordering": ["position"]},
        ),
        migrations.AddConstraint(model_name="imagegeneration", constraint=models.UniqueConstraint(fields=("owner", "idempotency_key"), name="unique_image_generation_idempotency")),
        migrations.AddIndex(model_name="imagegeneration", index=models.Index(fields=["owner", "-created_at"], name="image_studi_owner_i_4e9b37_idx")),
        migrations.AddConstraint(model_name="generatedimage", constraint=models.UniqueConstraint(fields=("generation", "position"), name="unique_generated_image_position")),
    ]
