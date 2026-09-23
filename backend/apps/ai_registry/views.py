from rest_framework import mixins, viewsets

from .models import AIModel
from .serializers import AIModelSerializer


class AIModelViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = AIModelSerializer

    def get_queryset(self):
        # Client catalog reads must never mutate model activation state. Models
        # are exposed only after an explicit admin activation/repair workflow.
        # GigaChat is intentionally internal-only: AUTO Router may use it, but
        # users never see or manually select the provider/model in the workspace.
        return AIModel.objects.filter(enabled=True).exclude(
            provider__slug="gigachat"
        ).select_related(
            "provider", "current_version"
        ).order_by("provider__priority", "display_name")
