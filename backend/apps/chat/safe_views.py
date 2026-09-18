from django.db.models import Q
from django.utils import timezone

from .ux_models import ConversationUIState
from .views import ConversationViewSet


class SafeConversationViewSet(ConversationViewSet):
    """Conversation API with non-destructive deletion for billing/audit safety."""

    def get_queryset(self):
        return super().get_queryset().filter(
            Q(ui_state__deleted_at__isnull=True) | Q(ui_state__isnull=True)
        )

    def perform_destroy(self, instance):
        state, _ = ConversationUIState.objects.get_or_create(
            conversation=instance,
            defaults={"owner": self.request.user},
        )
        state.deleted_at = timezone.now()
        state.is_pinned = False
        state.folder = None
        state.save(update_fields=["deleted_at", "is_pinned", "folder", "updated_at"])
