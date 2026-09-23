from django.db import migrations


DEFAULTS = {
    "economy": "gigachat-2-lite",
    "balanced": "gigachat-2-pro",
    "maximum": "gigachat-2-max",
}


def bootstrap_auto_tiers(apps, schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    ProviderApiKey = apps.get_model("ai_registry", "ProviderApiKey")
    AIModel = apps.get_model("ai_registry", "AIModel")
    RoutingPolicyVersion = apps.get_model("ai_registry", "RoutingPolicyVersion")

    provider = Provider.objects.filter(slug="gigachat").first()
    if provider is None:
        return
    healthy_key = ProviderApiKey.objects.filter(provider=provider, enabled=True, health_state="healthy").exists()
    if provider.health_state != "healthy" or not healthy_key:
        return

    Provider.objects.filter(pk=provider.pk).update(enabled=True, emergency_disabled=False)
    AIModel.objects.filter(provider=provider, slug__in=DEFAULTS.values()).update(enabled=True)

    policy = RoutingPolicyVersion.objects.filter(active=True).first()
    if policy is None:
        return
    thresholds = dict(policy.thresholds or {})
    tier_models = dict(thresholds.get("tier_models") or {})
    changed = False
    for mode, slug in DEFAULTS.items():
        if not tier_models.get(mode) and AIModel.objects.filter(slug=slug).exists():
            tier_models[mode] = slug
            changed = True
    if changed:
        thresholds["tier_models"] = tier_models
        RoutingPolicyVersion.objects.filter(pk=policy.pk).update(thresholds=thresholds)


class Migration(migrations.Migration):
    dependencies = [
        ("ai_registry", "0012_gigachat_standard_models"),
        ("billing", "0009_gigachat_standard_model_prices"),
    ]
    operations = [migrations.RunPython(bootstrap_auto_tiers, migrations.RunPython.noop)]
