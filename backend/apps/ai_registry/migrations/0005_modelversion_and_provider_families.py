import uuid

import django.db.models.deletion
from django.db import migrations, models


def bootstrap_model_versions(apps, _schema_editor):
    ai_model = apps.get_model("ai_registry", "AIModel")
    model_version = apps.get_model("ai_registry", "ModelVersion")
    for model in ai_model.objects.all().iterator():
        version = model_version.objects.create(
            model_id=model.id,
            version="legacy",
            exact_api_id=model.upstream_model,
            capabilities=model.capabilities,
            routing_tags=model.routing_tags,
            context_window=model.context_window,
            max_output_tokens=model.max_output_tokens,
            stage="active",
        )
        ai_model.objects.filter(pk=model.pk).update(current_version_id=version.id)


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0004_routingpolicyversion")]
    operations = [
        migrations.AlterField(
            model_name="provider",
            name="adapter_type",
            field=models.CharField(
                choices=[
                    ("echo", "Тестовый"),
                    ("openai_responses", "OpenAI Responses API"),
                    ("anthropic_messages", "Anthropic Messages API"),
                    ("deepseek_chat", "DeepSeek Chat API"),
                    ("gemini_generate_content", "Google Gemini API"),
                    ("xai_chat", "xAI Chat Completions API"),
                ],
                default="echo",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="ModelVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("version", models.SlugField(max_length=100)),
                ("exact_api_id", models.CharField(max_length=160)),
                ("capabilities", models.JSONField(blank=True, default=list)),
                ("routing_tags", models.JSONField(blank=True, default=list)),
                ("context_window", models.PositiveIntegerField(default=8192)),
                ("max_output_tokens", models.PositiveIntegerField(default=2048)),
                ("stage", models.CharField(choices=[("candidate", "Кандидат"), ("canary", "Canary"), ("active", "Активна"), ("retired", "Выведена")], default="candidate", max_length=16)),
                ("release_notes", models.TextField(blank=True)),
                ("eval_run_id", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("retired_at", models.DateTimeField(blank=True, null=True)),
                ("model", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="versions", to="ai_registry.aimodel")),
            ],
            options={"ordering": ["model__slug", "-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="modelversion",
            constraint=models.UniqueConstraint(fields=("model", "version"), name="unique_model_registry_version"),
        ),
        migrations.AddConstraint(
            model_name="modelversion",
            constraint=models.UniqueConstraint(condition=models.Q(("stage", "active")), fields=("model",), name="unique_active_version_per_model"),
        ),
        migrations.AddField(
            model_name="aimodel",
            name="current_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="active_for_models", to="ai_registry.modelversion"),
        ),
        migrations.RunPython(bootstrap_model_versions, migrations.RunPython.noop),
        migrations.CreateModel(
            name="ModelVersionTransition",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("action", models.CharField(choices=[("promote", "Продвижение"), ("rollback", "Откат")], max_length=16)),
                ("eval_run_id", models.UUIDField(blank=True, null=True)),
                ("reason", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("from_version", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="transitions_from", to="ai_registry.modelversion")),
                ("model", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="version_transitions", to="ai_registry.aimodel")),
                ("to_version", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transitions_to", to="ai_registry.modelversion")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
