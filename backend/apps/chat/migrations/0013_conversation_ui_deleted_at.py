from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("chat", "0012_conversation_ui")]

    operations = [
        migrations.AddField(
            model_name="conversationuistate",
            name="deleted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
