from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("image_studio", "0002_imagegeneration_conversation")]

    operations = [
        migrations.AddField(
            model_name="imagemodel",
            name="provider_price_matrix",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
