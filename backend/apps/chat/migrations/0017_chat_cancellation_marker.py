import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0016_explicit_auto_routing_mode"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatCancellationMarker",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("idempotency_hash", models.CharField(max_length=64, unique=True)),
                ("requested_at", models.DateTimeField(auto_now=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                (
                    "generation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cancellation_markers",
                        to="chat.generation",
                    ),
                ),
            ],
            options={"ordering": ["-requested_at"]},
        ),
        migrations.AddIndex(
            model_name="chatcancellationmarker",
            index=models.Index(
                fields=["generation", "expires_at"],
                name="chat_cancel_generation_idx",
            ),
        ),
    ]
