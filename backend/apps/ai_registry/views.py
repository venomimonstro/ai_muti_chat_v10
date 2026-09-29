from rest_framework import mixins, viewsets
from rest_framework.response import Response

from .models import AIModel
from .reliability import model_client_ready
from .serializers import AIModelSerializer


class AIModelViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = AIModelSerializer

    def get_queryset(self):
        # The raw queryset remains read-only. Customer visibility is decided by
        # the fail-closed runtime predicate in list(), not by stale enabled flags.
        return AIModel.objects.filter(enabled=True).select_related(
            "provider", "current_version"
        ).order_by("provider__priority", "display_name")

    def list(self, request, *args, **kwargs):
        # Never show dead/unverified models to customers. A model reappears only
        # after provider health, key health, version/upstream id and commercial
        # pricing are all verified again.
        ready = [model for model in self.get_queryset() if model_client_ready(model)]
        serializer = self.get_serializer(ready, many=True)
        return Response(serializer.data)
