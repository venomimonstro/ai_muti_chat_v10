from django.db import migrations


def seed_yandexgpt(apps, schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    AIModel = apps.get_model("ai_registry", "AIModel")

    provider, _created = Provider.objects.get_or_create(
        slug="yandexgpt",
        defaults={
            "name": "YandexGPT",
            "enabled": True,
            "emergency_disabled": False,
            "priority": 35,
            "region": "ru",
            # Runtime dispatch is provider-slug based. Reuse a real chat adapter
            # enum value so no schema/choice migration is needed.
            "adapter_type": "deepseek_chat",
            "api_base_url": "https://ai.api.cloud.yandex.net/v1",
            "auth_config": {},
            "health_state": "unknown",
        },
    )
    AIModel.objects.get_or_create(
        slug="yandexgpt",
        defaults={
            "provider": provider,
            "display_name": "YandexGPT",
            "upstream_model": "yandexgpt/latest",
            "enabled": True,
            "capabilities": ["text", "streaming"],
            "routing_tags": ["general", "ru"],
            "context_window": 8192,
            "max_output_tokens": 2048,
            # Zero buy price intentionally keeps the model fail-closed until the
            # administrator enters the real procurement prices.
            "input_price_rub_per_million": 0,
            "output_price_rub_per_million": 0,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0014_normalize_special_provider_adapters")]

    operations = [migrations.RunPython(seed_yandexgpt, migrations.RunPython.noop)]
