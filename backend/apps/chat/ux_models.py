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
