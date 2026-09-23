from django.db import migrations


def add_gigachat(apps, schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    AIModel = apps.get_model("ai_registry", "AIModel")
    ModelVersion = apps.get_model("ai_registry", "ModelVersion")

    provider, _ = Provider.objects.get_or_create(
        slug="gigachat",
        defaults={
            "name": "GigaChat API",
            "enabled": False,
            "priority": 60,
            # Routing is overridden by provider slug in adapter_for because
            # GigaChat uses OAuth rather than a static Bearer token.
            "adapter_type": "deepseek_chat",
            "api_base_url": "https://api.giga.chat/v1",
            "credential_env": "GIGACHAT_API_KEY",
        },
    )
    changed = False
    for field, value in {
        "name": "GigaChat API",
        "api_base_url": "https://api.giga.chat/v1",
        "credential_env": "GIGACHAT_API_KEY",
    }.items():
        if not getattr(provider, field):
            setattr(provider, field, value)
            changed = True
    if changed:
        provider.save()

    model, _ = AIModel.objects.get_or_create(
        slug="gigachat-2-max",
        defaults={
            "provider": provider,
            "display_name": "GigaChat 2 Max",
            "upstream_model": "GigaChat-2-Max",
            "enabled": False,
            "capabilities": ["text", "streaming"],
            "routing_tags": ["internal-auto-only", "gigachat-api"],
            "context_window": 32768,
            "max_output_tokens": 4096,
        },
    )
    if not model.current_version_id:
        version, _ = ModelVersion.objects.get_or_create(
            model=model,
            version="gigachat-api-v1",
            defaults={
                "exact_api_id": "GigaChat-2-Max",
                "capabilities": ["text", "streaming"],
                "routing_tags": ["internal-auto-only", "gigachat-api"],
                "context_window": 32768,
                "max_output_tokens": 4096,
                "stage": "active",
                "release_notes": "API-only GigaChat provider; never runs locally",
            },
        )
        model.current_version = version
        model.save(update_fields=["current_version"])


def remove_gigachat(apps, schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    provider = Provider.objects.filter(slug="gigachat").first()
    if provider and not provider.models.filter(enabled=True).exists():
        provider.models.all().delete()
        provider.delete()


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0009_openrouter_provider")]
    operations = [migrations.RunPython(add_gigachat, remove_gigachat)]
