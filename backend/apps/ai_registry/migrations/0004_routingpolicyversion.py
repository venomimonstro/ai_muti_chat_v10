import uuid
from django.db import migrations, models


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


def create_default_policy(apps, _schema_editor):
    policy = apps.get_model("ai_registry", "RoutingPolicyVersion")
    policy.objects.create(
        version="router-v1",
        active=True,
        mode_weights=DEFAULT_WEIGHTS,
        thresholds=DEFAULT_THRESHOLDS,
    )


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0003_alter_provider_options_aimodel_capabilities_and_more")]
    operations = [
        migrations.CreateModel(
            name="RoutingPolicyVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("version", models.SlugField(max_length=80, unique=True)),
                ("active", models.BooleanField(default=False)),
                ("mode_weights", models.JSONField(default=dict)),
                ("thresholds", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="routingpolicyversion",
            constraint=models.UniqueConstraint(condition=models.Q(("active", True)), fields=("active",), name="unique_active_routing_policy"),
        ),
        migrations.RunPython(create_default_policy, migrations.RunPython.noop),
    ]
