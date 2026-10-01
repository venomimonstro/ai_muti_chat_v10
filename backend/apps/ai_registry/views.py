from django.db.models import Prefetch
from rest_framework import mixins, viewsets
from rest_framework.response import Response

from .models import AIModel, RoutingTierAssignment
from .reliability import model_client_ready
from .serializers import AIModelSerializer


class AIModelViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = AIModelSerializer

    def get_queryset(self):
        # The raw queryset remains read-only. Customer visibility is decided by
        # the fail-closed runtime predicate in list(), not by stale enabled flags.
        return (
            AIModel.objects.filter(enabled=True)
            .select_related("provider", "current_version")
            .prefetch_related(
                Prefetch(
                    "routing_tiers",
                    queryset=RoutingTierAssignment.objects.filter(enabled=True).only(
                        "id", "tier", "model_id"
                    ),
                    to_attr="client_routing_tiers",
                )
            )
            .order_by("provider__priority", "display_name")
        )

    def list(self, request, *args, **kwargs):
        # Never show dead/unverified models to customers. A model reappears only
        # after provider health, key health, upstream id and commercial pricing are
        # verified again. Tier metadata is returned with the exact same ready rows so
        # the UI cannot advertise an explicit Simple/Medium/Complex pool that has no
        # runnable model.
        ready = [model for model in self.get_queryset() if model_client_ready(model)]
        context = self.get_serializer_context()
        context["routing_tiers_configured"] = RoutingTierAssignment.objects.filter(
            enabled=True
        ).exists()
        serializer = self.get_serializer(ready, many=True, context=context)
        return Response(serializer.data)
