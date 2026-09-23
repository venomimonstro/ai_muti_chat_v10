from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai_registry", "0010_gigachat_api_provider"),
    ]

    operations = [
        migrations.AddField(
            model_name="provider",
            name="auth_config",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
