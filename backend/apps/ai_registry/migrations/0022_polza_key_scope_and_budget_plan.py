from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0021_seed_existing_polza_allowlists")]
    operations = [
        migrations.AddField(
            model_name="providerapikey",
            name="model_scope_source",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="providerapikey",
            name="budget_plan",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
