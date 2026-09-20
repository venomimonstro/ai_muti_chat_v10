from rest_framework import mixins, viewsets

from .models import AIModel
from .reliability import ensure_safe_client_models
from .serializers import AIModelSerializer


class AIModelViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = AIModelSerializer

    def get_queryset(self):
        ensure_safe_client_models()
        return AIModel.objects.filter(enabled=True).select_related(
            "provider", "current_version"
        ).order_by("provider__priority", "display_name")
