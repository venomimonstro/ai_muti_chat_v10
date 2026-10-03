from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0019_provider_key_available_models")]
    operations = [
        migrations.AddField(
            model_name="providerapikey",
            name="allowed_models",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
