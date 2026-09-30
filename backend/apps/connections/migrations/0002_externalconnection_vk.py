from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("connections", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="externalconnection",
            name="kind",
            field=models.CharField(
                choices=[("wordpress", "WordPress"), ("vk", "ВКонтакте")],
                max_length=32,
            ),
        ),
    ]
