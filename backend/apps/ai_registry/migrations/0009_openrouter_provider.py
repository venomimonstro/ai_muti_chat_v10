from django.db import migrations


def add_openrouter_provider(apps, _schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    provider, created = Provider.objects.get_or_create(
        slug="openrouter",
        defaults={
            "name": "OpenRouter",
            "enabled": False,
            "emergency_disabled": False,
            "priority": 60,
            "region": "",
            # OpenRouter is OpenAI-compatible on /chat/completions, so the
            # existing OpenAI-compatible chat adapter is the correct runtime
            # transport. Keeping a separate provider row preserves routing,
            # billing, keys and procurement as independent OpenRouter state.
            "adapter_type": "xai_chat",
            "api_base_url": "https://openrouter.ai/api/v1",
            "credential_env": "OPENROUTER_API_KEY",
            "health_state": "unknown",
        },
    )
    if not created:
        changed = False
        desired = {
            "name": "OpenRouter",
            "adapter_type": "xai_chat",
            "api_base_url": "https://openrouter.ai/api/v1",
            "credential_env": "OPENROUTER_API_KEY",
        }
        for field, value in desired.items():
            if getattr(provider, field) != value:
                setattr(provider, field, value)
                changed = True
        if changed:
            provider.save(update_fields=list(desired.keys()))


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0008_normalize_active_routing_policy")]
    operations = [
        migrations.RunPython(add_openrouter_provider, migrations.RunPython.noop),
    ]
