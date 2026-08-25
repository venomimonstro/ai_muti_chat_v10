from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("evals", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="evalrun",
            name="model_version_id_snapshot",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        )
    ]
