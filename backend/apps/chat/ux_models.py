import uuid

from django.conf import settings
from django.db import models

from .models import Conversation


class ConversationFolder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_folders",
    )
    name = models.CharField(max_length=100)
    is_pinned = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "position", "name"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_folder_name_per_owner")
        ]


class ConversationUIState(models.Model):
    conversation = models.OneToOneField(
        Conversation,
        on_delete=models.CASCADE,
        related_name="ui_state",
        primary_key=True,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_ui_states",
    )
    folder = models.ForeignKey(
        ConversationFolder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversation_states",
    )
    is_pinned = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-updated_at"]


class ChatCancellationMarker(models.Model):
    """Durable Stop marker for one idempotent chat request.

    Redis remains the fast path, but a database marker guarantees that a user Stop
    survives cache outages/restarts and is visible to another backend process.
    The idempotency key itself is never stored; only its SHA-256 digest is persisted.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_cancellation_markers",
    )
    idempotency_hash = models.CharField(max_length=64)
    generation = models.ForeignKey(
        "chat.Generation",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="cancellation_markers",
    )
    requested_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "idempotency_hash"],
                name="unique_chat_cancel_owner_idem",
            )
        ]
        indexes = [models.Index(fields=["generation", "expires_at"], name="chat_cancel_generation_idx")]
