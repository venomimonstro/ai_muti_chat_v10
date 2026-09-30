from django.db import migrations


def normalize_special_provider_adapters(apps, schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    Provider.objects.filter(
        slug__in=("gigachat", "openrouter"),
        adapter_type="echo",
    ).update(adapter_type="openai_responses")


def reverse_noop(apps, schema_editor):
    # Never restore a production provider to the test Echo adapter.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("ai_registry", "0013_routing_tier_assignment"),
    ]

    operations = [
        migrations.RunPython(normalize_special_provider_adapters, reverse_noop),
    ]
