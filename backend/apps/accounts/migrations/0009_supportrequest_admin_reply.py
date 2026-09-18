from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_supportrequest_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportrequest",
            name="admin_reply",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="supportrequest",
            name="replied_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supportrequest",
            name="replied_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="support_replies",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
