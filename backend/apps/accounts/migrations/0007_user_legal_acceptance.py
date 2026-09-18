from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_usersecurityprofile")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="legal_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="legal_version",
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
