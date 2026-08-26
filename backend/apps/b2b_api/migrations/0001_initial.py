# Generated manually for Sprint 23.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("ai_registry", "0005_modelversion_and_provider_families"),
        ("billing", "0004_cost_protection"),
    ]
    operations = [
        migrations.CreateModel(
            name="Organization",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=160)),
                ("slug", models.SlugField(unique=True)),
                ("monthly_limit_rub", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("billing_user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="funded_organizations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="OrganizationMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("owner", "Владелец"), ("admin", "Администратор"), ("billing", "Биллинг"), ("developer", "Разработчик"), ("member", "Участник"), ("viewer", "Наблюдатель")], max_length=16)),
                ("status", models.CharField(choices=[("active", "Активен"), ("suspended", "Приостановлен"), ("removed", "Удалён")], default="active", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="b2b_api.organization")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="organization_memberships", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="APIKey",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120)),
                ("prefix", models.CharField(db_index=True, max_length=24, unique=True)),
                ("secret_hash", models.CharField(max_length=64)),
                ("scopes", models.JSONField(default=list)),
                ("allowed_models", models.JSONField(blank=True, default=list)),
                ("allowed_endpoints", models.JSONField(blank=True, default=list)),
                ("monthly_limit_rub", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("rate_limit_per_minute", models.PositiveIntegerField(default=60)),
                ("max_concurrency", models.PositiveIntegerField(default=2)),
                ("ip_allowlist", models.JSONField(blank=True, default=list)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_api_keys", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="api_keys", to="b2b_api.organization")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="APIUsage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("response_id", models.CharField(max_length=80, unique=True)),
                ("idempotency_key", models.CharField(blank=True, max_length=160)),
                ("request_hash", models.CharField(max_length=64)),
                ("endpoint", models.CharField(default="chat.completions", max_length=80)),
                ("state", models.CharField(choices=[("running", "Выполняется"), ("completed", "Завершён"), ("failed", "Ошибка")], default="running", max_length=16)),
                ("estimated_cost_rub", models.DecimalField(decimal_places=4, max_digits=14)),
                ("provider_cost_rub", models.DecimalField(decimal_places=4, default=0, max_digits=14)),
                ("charged_rub", models.DecimalField(decimal_places=4, default=0, max_digits=14)),
                ("prompt_tokens", models.PositiveIntegerField(default=0)),
                ("completion_tokens", models.PositiveIntegerField(default=0)),
                ("provider_request_id", models.CharField(blank=True, max_length=160)),
                ("response_text", models.TextField(blank=True)),
                ("error_code", models.CharField(blank=True, max_length=80)),
                ("latency_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("pricing_snapshot", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("api_key", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="usage", to="b2b_api.apikey")),
                ("model", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="api_usage", to="ai_registry.aimodel")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="usage", to="b2b_api.organization")),
                ("reservation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="api_usage", to="billing.balancereservation")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("action", models.CharField(max_length=80)),
                ("target_id", models.CharField(blank=True, max_length=80)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="organization_audit_log", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audit_log", to="b2b_api.organization")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(model_name="organizationmembership", constraint=models.UniqueConstraint(fields=("organization", "user"), name="unique_organization_member")),
        migrations.AddConstraint(model_name="apiusage", constraint=models.UniqueConstraint(condition=models.Q(("idempotency_key", ""), _negated=True), fields=("api_key", "idempotency_key"), name="unique_api_key_idempotency")),
        migrations.AddIndex(model_name="apiusage", index=models.Index(fields=["organization", "created_at"], name="b2b_api_api_organiz_4437a8_idx")),
        migrations.AddIndex(model_name="apiusage", index=models.Index(fields=["api_key", "state", "created_at"], name="b2b_api_api_api_key_cb00ca_idx")),
    ]
