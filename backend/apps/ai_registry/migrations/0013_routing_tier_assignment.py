import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("ai_registry", "0012_gigachat_standard_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="RoutingTierAssignment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tier", models.CharField(choices=[("economy", "Простой"), ("balanced", "Средний"), ("maximum", "Сложный")], max_length=16)),
                ("priority", models.PositiveIntegerField(default=100)),
                ("enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("model", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="routing_tiers", to="ai_registry.aimodel")),
            ],
            options={"ordering": ["tier", "priority", "model__display_name"]},
        ),
        migrations.AddConstraint(
            model_name="routingtierassignment",
            constraint=models.UniqueConstraint(fields=("tier", "model"), name="unique_model_per_routing_tier"),
        ),
    ]
