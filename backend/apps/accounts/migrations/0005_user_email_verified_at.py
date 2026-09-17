from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_userpreference_auto_memory_default_scope_and_more")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
