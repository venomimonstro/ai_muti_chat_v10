from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("github_integration", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="githubinstallation",
            name="user_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
