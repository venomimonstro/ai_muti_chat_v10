import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("connections", "0002_externalconnection_vk"),
        ("agents", "0003_agentschedule"),
        ("projects", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SMMContentPlan",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(default="Контент-план VK", max_length=180)),
                ("business_context", models.TextField(blank=True)),
                ("goal", models.TextField(blank=True)),
                ("audience", models.TextField(blank=True)),
                ("tone", models.CharField(blank=True, max_length=120)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("status", models.CharField(choices=[("draft", "Черновик"), ("active", "Активен"), ("completed", "Завершён"), ("archived", "Архив")], db_index=True, default="draft", max_length=16)),
                ("auto_publish", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("agent", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="smm_content_plans", to="agents.agent")),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="smm_content_plans", to="connections.externalconnection")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="smm_content_plans", to=settings.AUTH_USER_MODEL)),
                ("project", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="smm_content_plans", to="projects.project")),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="SMMContentItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=220)),
                ("topic", models.CharField(blank=True, max_length=240)),
                ("objective", models.CharField(blank=True, max_length=240)),
                ("content", models.TextField(blank=True)),
                ("cta", models.CharField(blank=True, max_length=300)),
                ("hashtags", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(choices=[("idea", "Идея"), ("draft", "Черновик"), ("approved", "Одобрен"), ("scheduled", "Запланирован"), ("publishing", "Публикуется"), ("published", "Опубликован"), ("failed", "Ошибка")], db_index=True, default="idea", max_length=16)),
                ("scheduled_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("media_source", models.CharField(choices=[("none", "Без изображения"), ("generated", "Сгенерировано AI"), ("stock", "Бесплатный фотосток"), ("uploaded", "Загружено")], default="none", max_length=16)),
                ("media_url", models.URLField(blank=True, max_length=1000)),
                ("media_prompt", models.TextField(blank=True)),
                ("media_attribution", models.CharField(blank=True, max_length=500)),
                ("vk_attachment", models.CharField(blank=True, max_length=255)),
                ("external_post_id", models.CharField(blank=True, db_index=True, max_length=160)),
                ("publish_error", models.CharField(blank=True, max_length=500)),
                ("sort_order", models.PositiveIntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="connections.smmcontentplan")),
            ],
            options={"ordering": ["scheduled_at", "sort_order", "created_at"]},
        ),
        migrations.CreateModel(
            name="SMMPublicationAttempt",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("idempotency_key", models.CharField(max_length=180, unique=True)),
                ("state", models.CharField(choices=[("started", "Запущена"), ("completed", "Опубликована"), ("failed", "Ошибка"), ("skipped", "Пропущена")], default="started", max_length=16)),
                ("external_post_id", models.CharField(blank=True, max_length=160)),
                ("error_code", models.CharField(blank=True, max_length=80)),
                ("error_message", models.CharField(blank=True, max_length=500)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="publication_attempts", to="connections.smmcontentitem")),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.AddIndex(model_name="smmcontentplan", index=models.Index(fields=["owner", "status"], name="connections_owner_i_5ab4a5_idx")),
        migrations.AddIndex(model_name="smmcontentplan", index=models.Index(fields=["connection", "period_start"], name="connections_connect_b14428_idx")),
        migrations.AddIndex(model_name="smmcontentitem", index=models.Index(fields=["plan", "status"], name="connections_plan_id_218d3c_idx")),
    ]
