from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ai_registry", "0005_modelversion_and_provider_families"),
    ]

    operations = [
        migrations.AddField(
            model_name="provider",
            name="credential_secret",
            field=models.TextField(blank=True, editable=False),
        ),
    ]
