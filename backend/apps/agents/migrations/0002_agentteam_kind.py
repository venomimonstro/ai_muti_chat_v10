from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("agents", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentteam",
            name="kind",
            field=models.CharField(
                choices=[
                    ("generic", "Универсальная"),
                    ("marketing", "Маркетинг"),
                    ("content", "Контент"),
                    ("sales", "Продажи"),
                    ("development", "Разработка"),
                ],
                db_index=True,
                default="generic",
                max_length=24,
            ),
        ),
    ]
