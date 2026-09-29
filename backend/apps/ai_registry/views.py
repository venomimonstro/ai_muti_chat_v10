from rest_framework import mixins, viewsets

from .models import AIModel
from .serializers import AIModelSerializer


class AIModelViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = AIModelSerializer

    def get_queryset(self):
        # Client catalog reads never mutate activation state. Internal upstream
        # providers may still be exposed under a product-safe public identity by
        # the serializer (for example GigaChat -> LLM System).
        return AIModel.objects.filter(enabled=True).select_related(
            "provider", "current_version"
        ).order_by("provider__priority", "display_name")
