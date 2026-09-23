from django.db import migrations
from django.utils import timezone


MODELS = [
    ("gigachat-2-lite", "GigaChat 2 Lite", "GigaChat-2"),
    ("gigachat-2-pro", "GigaChat 2 Pro", "GigaChat-2-Pro"),
    ("gigachat-2-max", "GigaChat 2 Max", "GigaChat-2-Max"),
]


def register_standard_models(apps, schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    AIModel = apps.get_model("ai_registry", "AIModel")
    ModelVersion = apps.get_model("ai_registry", "ModelVersion")
    provider = Provider.objects.filter(slug="gigachat").first()
    if provider is None:
        return
    now = timezone.now()
    for slug, display_name, upstream in MODELS:
        model, _ = AIModel.objects.get_or_create(
            slug=slug,
            defaults={
                "provider": provider,
                "display_name": display_name,
                "upstream_model": upstream,
                "enabled": False,
                "capabilities": ["text", "streaming"],
                "routing_tags": ["general", "russian"],
                "context_window": 128000,
                "max_output_tokens": 4096,
            },
        )
        changed = []
        if not model.upstream_model:
            model.upstream_model = upstream
            changed.append("upstream_model")
        if model.current_version_id is None:
            version = ModelVersion.objects.create(
                model=model,
                version="official-2026-09",
                exact_api_id=upstream,
                capabilities=["text", "streaming"],
                routing_tags=["general", "russian"],
                context_window=128000,
                max_output_tokens=4096,
                stage="active",
                release_notes="Стандартный GigaChat API model ID",
                activated_at=now,
            )
            model.current_version = version
            changed.append("current_version")
        if changed:
            model.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0011_provider_auth_config")]
    operations = [migrations.RunPython(register_standard_models, migrations.RunPython.noop)]
