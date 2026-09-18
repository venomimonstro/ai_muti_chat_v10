import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0011_scope_idempotency_by_user"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConversationFolder",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=100)),
                ("is_pinned", models.BooleanField(default=False)),
                ("position", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conversation_folders", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-is_pinned", "position", "name"]},
        ),
        migrations.AddConstraint(
            model_name="conversationfolder",
            constraint=models.UniqueConstraint(fields=("owner", "name"), name="unique_folder_name_per_owner"),
        ),
        migrations.CreateModel(
            name="ConversationUIState",
            fields=[
                ("conversation", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name="ui_state", serialize=False, to="chat.conversation")),
                ("is_pinned", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("folder", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="conversation_states", to="chat.conversationfolder")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conversation_ui_states", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-is_pinned", "-updated_at"]},
        ),
    ]
