import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("agents", "0003_agentschedule"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExternalConnection",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("wordpress", "WordPress")], max_length=32)),
                ("name", models.CharField(max_length=160)),
                ("base_url", models.URLField(max_length=500)),
                ("username", models.CharField(blank=True, max_length=255)),
                ("secret_encrypted", models.TextField(blank=True, editable=False)),
                ("enabled", models.BooleanField(default=True)),
                ("health_state", models.CharField(choices=[("unknown", "Не проверено"), ("healthy", "Работает"), ("degraded", "Ошибка"), ("disabled", "Отключено")], default="unknown", max_length=16)),
                ("last_error", models.CharField(blank=True, max_length=240)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="external_connections", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["kind", "name"]},
        ),
        migrations.CreateModel(
            name="AgentConnectionBinding",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("purpose", models.CharField(default="publish", max_length=80)),
                ("enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("agent", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="connection_bindings", to="agents.agent")),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_bindings", to="connections.externalconnection")),
            ],
            options={"ordering": ["purpose", "created_at"]},
        ),
        migrations.AddConstraint(
            model_name="externalconnection",
            constraint=models.UniqueConstraint(fields=("owner", "kind", "name"), name="unique_owner_connection_name"),
        ),
        migrations.AddConstraint(
            model_name="agentconnectionbinding",
            constraint=models.UniqueConstraint(fields=("agent", "connection", "purpose"), name="unique_agent_connection_purpose"),
        ),
    ]
