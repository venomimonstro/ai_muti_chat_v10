from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .ux_models import ConversationUIState
from .views import ConversationViewSet


class ClientConversationViewSet(ConversationViewSet):
    """User-facing conversation CRUD with non-destructive deletion."""

    def get_queryset(self):
        return super().get_queryset().filter(
            Q(ui_state__isnull=True) | Q(ui_state__deleted_at__isnull=True)
        )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        conversation = self.get_object()
        state, _ = ConversationUIState.objects.select_for_update().get_or_create(
            conversation=conversation,
            defaults={"owner": request.user},
        )
        state.deleted_at = timezone.now()
        state.folder = None
        state.is_pinned = False
        state.save(update_fields=["deleted_at", "folder", "is_pinned", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
