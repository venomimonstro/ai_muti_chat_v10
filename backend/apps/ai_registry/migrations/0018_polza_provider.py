from django.db import migrations


def add_polza_provider(apps, _schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    provider, _created = Provider.objects.get_or_create(
        slug="polza",
        defaults={
            "name": "Polza.ai",
            "enabled": False,
            "emergency_disabled": False,
            "priority": 35,
            "region": "RU",
            "adapter_type": "xai_chat",
            "api_base_url": "https://polza.ai/api/v1",
            "credential_env": "POLZA_API_KEY",
            "health_state": "unknown",
            "auth_config": {"catalog_source": "models_api", "multi_model_gateway": True},
        },
    )
    desired = {
        "name": "Polza.ai",
        "adapter_type": "xai_chat",
        "api_base_url": "https://polza.ai/api/v1",
        "credential_env": "POLZA_API_KEY",
        "region": "RU",
    }
    changed = []
    for field, value in desired.items():
        if getattr(provider, field) != value:
            setattr(provider, field, value)
            changed.append(field)
    config = dict(provider.auth_config or {})
    if config.get("catalog_source") != "models_api":
        config["catalog_source"] = "models_api"
        changed.append("auth_config")
    if config.get("multi_model_gateway") is not True:
        config["multi_model_gateway"] = True
        if "auth_config" not in changed:
            changed.append("auth_config")
    provider.auth_config = config
    if changed:
        provider.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0017_hubai_admin_only_credentials")]
    operations = [migrations.RunPython(add_polza_provider, migrations.RunPython.noop)]
