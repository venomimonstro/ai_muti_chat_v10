import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai_registry", "0004_routingpolicyversion"),
        ("chat", "0007_conversationsummary"),
    ]
    operations = [
        migrations.AddField(
            model_name="conversation",
            name="routing_mode",
            field=models.CharField(choices=[("manual", "Вручную"), ("economy", "Эконом"), ("balanced", "Баланс"), ("maximum", "Максимум")], default="manual", max_length=16),
        ),
        migrations.CreateModel(
            name="RoutingDecision",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("mode", models.CharField(choices=[("manual", "Вручную"), ("economy", "Эконом"), ("balanced", "Баланс"), ("maximum", "Максимум")], max_length=16)),
                ("task_taxonomy", models.CharField(max_length=32)),
                ("classification_confidence", models.DecimalField(decimal_places=4, max_digits=5)),
                ("required_capabilities", models.JSONField(default=list)),
                ("signals", models.JSONField(default=dict)),
                ("candidate_snapshot", models.JSONField(default=list)),
                ("explanation", models.TextField()),
                ("estimated_input_tokens", models.PositiveIntegerField()),
                ("estimated_output_tokens", models.PositiveIntegerField()),
                ("estimated_cost_rub", models.DecimalField(decimal_places=4, max_digits=14)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("generation", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="routing_decision", to="chat.generation")),
                ("policy", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="decisions", to="ai_registry.routingpolicyversion")),
                ("selected_model", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="routing_selections", to="ai_registry.aimodel")),
            ],
        ),
    ]
