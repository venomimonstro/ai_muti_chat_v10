from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0006_provider_credential_secret")]

    operations = [
        migrations.CreateModel(
            name="ProviderApiKey",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("label", models.CharField(default="Основной ключ", max_length=120)),
                ("secret_encrypted", models.TextField(editable=False)),
                ("enabled", models.BooleanField(default=True)),
                ("priority", models.PositiveIntegerField(default=100)),
                ("health_state", models.CharField(choices=[("unknown", "Не проверен"), ("healthy", "Работает"), ("degraded", "Ошибка"), ("disabled", "Отключён")], default="unknown", max_length=16)),
                ("last_error_code", models.CharField(blank=True, max_length=80)),
                ("last_latency_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("balance_amount", models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True)),
                ("balance_currency", models.CharField(blank=True, max_length=12)),
                ("balance_supported", models.BooleanField(default=False)),
                ("balance_checked_at", models.DateTimeField(blank=True, null=True)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("provider", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="api_keys", to="ai_registry.provider")),
            ],
            options={"ordering": ["priority", "created_at"]},
        ),
        migrations.AddConstraint(
            model_name="providerapikey",
            constraint=models.UniqueConstraint(fields=("provider", "label"), name="unique_provider_api_key_label"),
        ),
    ]
