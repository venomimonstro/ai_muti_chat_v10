from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("procurement", "0005_provider_purchase_lifecycle")]
    operations = [
        migrations.AddField(
            model_name="providerpurchase",
            name="pricing_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
