from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0018_polza_provider")]
    operations = [
        migrations.AddField(
            model_name="providerapikey",
            name="available_models",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
