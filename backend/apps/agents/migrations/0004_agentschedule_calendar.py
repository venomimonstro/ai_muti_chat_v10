from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("agents", "0003_agentschedule")]

    operations = [
        migrations.AddField(
            model_name="agentschedule",
            name="cadence",
            field=models.CharField(
                choices=[
                    ("interval", "Через интервал"),
                    ("daily", "Каждый день"),
                    ("weekdays", "По будням"),
                    ("weekly", "По выбранным дням"),
                ],
                default="interval",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="agentschedule",
            name="local_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agentschedule",
            name="timezone_name",
            field=models.CharField(default="Europe/Moscow", max_length=64),
        ),
        migrations.AddField(
            model_name="agentschedule",
            name="weekdays",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
