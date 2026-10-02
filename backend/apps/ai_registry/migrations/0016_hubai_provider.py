from django.db import migrations
from django.utils import timezone


MODELS = [
    (
        "hubai-deepseek-chat-fast",
        "DeepSeek V3 Fast",
        "deepseek-chat-fast",
        ["text", "streaming"],
        ["general", "fast", "deepseek", "hubai"],
    ),
    (
        "hubai-deepseek-reasoner-fast",
        "DeepSeek R1 Fast",
        "deepseek-reasoner-fast",
        ["text", "streaming", "reasoning"],
        ["reasoning", "fast", "deepseek", "hubai"],
    ),
    (
        "hubai-deepseek-chat",
        "DeepSeek V3",
        "deepseek-chat",
        ["text", "streaming"],
        ["general", "deepseek", "hubai"],
    ),
    (
        "hubai-deepseek-reasoner",
        "DeepSeek R1",
        "deepseek-reasoner",
        ["text", "streaming", "reasoning"],
        ["reasoning", "deepseek", "hubai"],
    ),
]


def seed_hubai(apps, _schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    AIModel = apps.get_model("ai_registry", "AIModel")
    ModelVersion = apps.get_model("ai_registry", "ModelVersion")

    provider, _created = Provider.objects.get_or_create(
        slug="hubai",
        defaults={
            "name": "HubAI",
            "enabled": False,
            "emergency_disabled": False,
            "priority": 45,
            "region": "",
            # HubAI exposes an OpenAI/DeepSeek-compatible /chat/completions API.
            # Reuse the hardened DeepSeek chat transport while keeping routing,
            # credentials, health, procurement and billing isolated by provider.
            "adapter_type": "deepseek_chat",
            "api_base_url": "https://hubai.loe.gg/v1",
            "credential_env": "",
            "health_state": "unknown",
        },
    )

    desired_provider = {
        "name": "HubAI",
        "adapter_type": "deepseek_chat",
        "api_base_url": "https://hubai.loe.gg/v1",
        "credential_env": "",
    }
    changed = []
    for field, value in desired_provider.items():
        if getattr(provider, field) != value:
            setattr(provider, field, value)
            changed.append(field)
    if changed:
        provider.save(update_fields=changed)

    now = timezone.now()
    for index, (slug, display_name, upstream, capabilities, routing_tags) in enumerate(MODELS):
        model, _ = AIModel.objects.get_or_create(
            slug=slug,
            defaults={
                "provider": provider,
                "display_name": display_name,
                "upstream_model": upstream,
                # Commercial readiness requires reviewed pricing/funding before
                # customer publication, so a deploy can never expose zero-price
                # models by accident.
                "enabled": False,
                "capabilities": capabilities,
                "routing_tags": routing_tags,
                "context_window": 8192,
                "max_output_tokens": 4096,
                "input_price_rub_per_million": 0,
                "output_price_rub_per_million": 0,
            },
        )
        model_changes = []
        if model.provider_id != provider.id:
            model.provider = provider
            model_changes.append("provider")
        if model.display_name != display_name:
            model.display_name = display_name
            model_changes.append("display_name")
        if model.upstream_model != upstream:
            model.upstream_model = upstream
            model_changes.append("upstream_model")
        if model.current_version_id is None:
            version = ModelVersion.objects.create(
                model=model,
                version=f"hubai-2026-10-{index + 1}",
                exact_api_id=upstream,
                capabilities=capabilities,
                routing_tags=routing_tags,
                context_window=model.context_window,
                max_output_tokens=model.max_output_tokens,
                stage="active",
                release_notes="HubAI OpenAI-compatible DeepSeek model",
                activated_at=now,
            )
            model.current_version = version
            model_changes.append("current_version")
        if model_changes:
            model.save(update_fields=model_changes)


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0015_yandexgpt_provider")]
    operations = [migrations.RunPython(seed_hubai, migrations.RunPython.noop)]
