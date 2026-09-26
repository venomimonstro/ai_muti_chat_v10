from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("agents", "0004_agentschedule_calendar"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentWebhookTrigger",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=160)),
                ("objective", models.TextField(blank=True)),
                ("secret_hash", models.CharField(max_length=256)),
                ("enabled", models.BooleanField(default=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("agent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="webhook_triggers", to="agents.agent")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_webhook_triggers", to=settings.AUTH_USER_MODEL)),
                ("team", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="webhook_triggers", to="agents.agentteam")),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="AgentWebhookDelivery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_id", models.CharField(max_length=160)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("state", models.CharField(choices=[("pending", "Ожидает"), ("queued", "Передано в очередь"), ("failed", "Ошибка")], default="pending", max_length=16)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="webhook_deliveries", to="agents.agentrun")),
                ("trigger", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deliveries", to="agents.agentwebhooktrigger")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="agentwebhooktrigger",
            constraint=models.CheckConstraint(
                condition=(models.Q(("agent__isnull", False), ("team__isnull", True)) | models.Q(("agent__isnull", True), ("team__isnull", False))),
                name="agent_webhook_exactly_one_subject",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentwebhookdelivery",
            constraint=models.UniqueConstraint(fields=("trigger", "event_id"), name="unique_agent_webhook_event"),
        ),
        migrations.AddIndex(
            model_name="agentwebhooktrigger",
            index=models.Index(fields=["owner", "enabled"], name="agents_webhook_owner_enabled_idx"),
        ),
        migrations.AddIndex(
            model_name="agentwebhookdelivery",
            index=models.Index(fields=["state", "created_at"], name="agents_webhook_state_created_idx"),
        ),
    ]
