# Generated manually for Sprint 25.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("admin_ops", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="ComplianceSignoff",
            fields=[
                ("key", models.SlugField(max_length=100, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=200)),
                ("status", models.CharField(choices=[("pending", "Ожидает"), ("approved", "Подтверждено"), ("blocked", "Блокирует запуск")], default="pending", max_length=16)),
                ("evidence_reference", models.CharField(blank=True, max_length=400)),
                ("notes", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="compliance_signoffs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["key"]},
        ),
        migrations.CreateModel(
            name="StatusIncident",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=200)),
                ("message", models.TextField()),
                ("impact", models.CharField(choices=[("minor", "Незначительный"), ("major", "Серьёзный"), ("critical", "Критический")], max_length=16)),
                ("state", models.CharField(choices=[("investigating", "Расследуется"), ("monitoring", "Наблюдение"), ("resolved", "Устранён")], default="investigating", max_length=20)),
                ("affected_components", models.JSONField(default=list)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_status_incidents", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-started_at"]},
        ),
    ]
