# Generated manually for Sprint 24.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="FeatureFlag",
            fields=[
                ("key", models.SlugField(max_length=100, primary_key=True, serialize=False)),
                ("description", models.CharField(blank=True, max_length=300)),
                ("enabled", models.BooleanField(default=False)),
                ("rollout_percent", models.PositiveSmallIntegerField(default=0)),
                ("allow_user_ids", models.JSONField(blank=True, default=list)),
                ("deny_user_ids", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="updated_feature_flags", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["key"]},
        ),
        migrations.CreateModel(
            name="SecurityEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("category", models.CharField(max_length=80)),
                ("severity", models.CharField(choices=[("info", "Информация"), ("warning", "Предупреждение"), ("critical", "Критический")], default="warning", max_length=16)),
                ("status", models.CharField(choices=[("open", "Открыт"), ("investigating", "Расследуется"), ("resolved", "Закрыт")], default="open", max_length=16)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("summary", models.CharField(max_length=240)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reported_security_events", to=settings.AUTH_USER_MODEL)),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="resolved_security_events", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="security_events", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ReleaseRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("version", models.CharField(max_length=80, unique=True)),
                ("commit_sha", models.CharField(max_length=64)),
                ("environment", models.CharField(default="production", max_length=40)),
                ("state", models.CharField(choices=[("draft", "Черновик"), ("canary", "Canary"), ("rolling", "Поэтапный"), ("stable", "Для всех"), ("rolled_back", "Откатан")], default="draft", max_length=20)),
                ("rollout_percent", models.PositiveSmallIntegerField(default=0)),
                ("allow_user_ids", models.JSONField(blank=True, default=list)),
                ("health_snapshot", models.JSONField(blank=True, default=dict)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_releases", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="BackupRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("database", "База данных"), ("media", "Медиа"), ("full", "Полный")], max_length=16)),
                ("status", models.CharField(choices=[("requested", "Запрошен"), ("running", "Выполняется"), ("succeeded", "Создан"), ("failed", "Ошибка"), ("verified", "Проверен"), ("restored", "Тест восстановления пройден")], default="requested", max_length=16)),
                ("storage_reference", models.CharField(blank=True, max_length=300)),
                ("size_bytes", models.PositiveBigIntegerField(blank=True, null=True)),
                ("checksum_sha256", models.CharField(blank=True, max_length=64)),
                ("notes", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("restored_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="requested_backups", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="AdminAuditEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("action", models.CharField(max_length=100)),
                ("target_type", models.CharField(max_length=80)),
                ("target_id", models.CharField(blank=True, max_length=100)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("request_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="admin_audit_events", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
