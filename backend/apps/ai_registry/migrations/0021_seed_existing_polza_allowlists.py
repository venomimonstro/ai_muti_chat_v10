from django.db import migrations


def seed_existing_polza_allowlists(apps, _schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    ProviderApiKey = apps.get_model("ai_registry", "ProviderApiKey")
    AIModel = apps.get_model("ai_registry", "AIModel")

    provider = Provider.objects.filter(slug="polza").first()
    if provider is None:
        return

    configured = list(
        AIModel.objects.filter(provider=provider)
        .exclude(upstream_model="")
        .values_list("upstream_model", flat=True)
    )
    configured = list(dict.fromkeys(str(value) for value in configured if value))
    if not configured:
        return

    for key in ProviderApiKey.objects.filter(provider=provider):
        if key.allowed_models:
            continue
        key.allowed_models = configured
        key.save(update_fields=["allowed_models"])


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0020_provider_key_allowed_models")]
    operations = [
        migrations.RunPython(
            seed_existing_polza_allowlists,
            migrations.RunPython.noop,
        )
    ]
