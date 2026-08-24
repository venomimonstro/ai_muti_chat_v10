import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("chat", "0006_conversation_memory_enabled_and_more")]

    operations = [
        migrations.CreateModel(
            name="ConversationSummary",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("content", models.TextField(blank=True)),
                ("source_message_count", models.PositiveIntegerField(default=0)),
                ("token_estimate", models.PositiveIntegerField(default=0)),
                ("version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("conversation", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="rolling_summary", to="chat.conversation")),
                ("through_message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="summary_checkpoints", to="chat.message")),
            ],
        )
    ]
