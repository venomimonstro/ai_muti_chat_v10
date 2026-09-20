from django.db import migrations


DEFAULT_WEIGHTS = {
    "economy": {"quality": 0.25, "cost": 0.55, "latency": 0.15, "health": 0.05},
    "balanced": {"quality": 0.50, "cost": 0.25, "latency": 0.20, "health": 0.05},
    "maximum": {"quality": 0.75, "cost": 0.05, "latency": 0.15, "health": 0.05},
}
DEFAULT_THRESHOLDS = {
    "default_quality": 0.55,
    "economy_min_quality": 0.60,
    "fallback_price_multiplier": 1.50,
    "unknown_latency_ms": 1500,
}


def normalize_active_policy(apps, _schema_editor):
    Policy = apps.get_model("ai_registry", "RoutingPolicyVersion")
    policies = list(Policy.objects.filter(active=True))
    if not policies:
        Policy.objects.create(
            version="router-v1-recovered",
            active=True,
            mode_weights=DEFAULT_WEIGHTS,
            thresholds=DEFAULT_THRESHOLDS,
        )
        return

    for policy in policies:
        raw_weights = policy.mode_weights if isinstance(policy.mode_weights, dict) else {}
        merged_weights = {}
        for mode, defaults in DEFAULT_WEIGHTS.items():
            override = raw_weights.get(mode)
            merged_weights[mode] = {
                **defaults,
                **(override if isinstance(override, dict) else {}),
            }
        raw_thresholds = policy.thresholds if isinstance(policy.thresholds, dict) else {}
        policy.mode_weights = merged_weights
        policy.thresholds = {**DEFAULT_THRESHOLDS, **raw_thresholds}
        policy.save(update_fields=["mode_weights", "thresholds"])


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0007_provider_api_key_pool")]
    operations = [
        migrations.RunPython(normalize_active_policy, migrations.RunPython.noop),
    ]
