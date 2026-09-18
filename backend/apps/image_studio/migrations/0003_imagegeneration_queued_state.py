from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("image_studio", "0002_imagegeneration_conversation")]

    operations = [
        migrations.AlterField(
            model_name="imagegeneration",
            name="state",
            field=models.CharField(
                choices=[
                    ("queued", "В очереди"),
                    ("running", "Выполняется"),
                    ("completed", "Готово"),
                    ("failed", "Ошибка"),
                ],
                default="running",
                max_length=16,
            ),
        ),
    ]
