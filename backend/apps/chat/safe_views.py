from django.db.models import Q
from django.utils import timezone

from .models import Conversation
from .ux_models import ConversationUIState
from .views import ConversationViewSet


class SafeConversationViewSet(ConversationViewSet):
    """Conversation API with non-destructive deletion for billing/audit safety."""

    def get_queryset(self):
        visible = Q(ui_state__deleted_at__isnull=True) | Q(ui_state__isnull=True)
        if self.action == "destroy":
            return Conversation.objects.filter(owner=self.request.user).filter(visible)
        return super().get_queryset().filter(visible)

    def perform_destroy(self, instance):
        state, _ = ConversationUIState.objects.get_or_create(
            conversation=instance,
            defaults={"owner": self.request.user},
        )
        state.deleted_at = timezone.now()
        state.is_pinned = False
        state.folder = None
        state.save(update_fields=["deleted_at", "is_pinned", "folder", "updated_at"])
