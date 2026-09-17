from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_user_email_verified_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserSecurityProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("totp_secret_encrypted", models.TextField(blank=True)),
                ("mfa_enabled", models.BooleanField(default=False)),
                ("recovery_code_hashes", models.JSONField(blank=True, default=list)),
                ("mfa_enabled_at", models.DateTimeField(blank=True, null=True)),
                ("last_mfa_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="security_profile", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
