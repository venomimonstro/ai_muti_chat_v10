from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import AgentConnectionBinding, ExternalConnection
from .serializers import AgentConnectionBindingSerializer, ExternalConnectionSerializer
from .wordpress import check_wordpress


class ExternalConnectionViewSet(viewsets.ModelViewSet):
    serializer_class = ExternalConnectionSerializer

    def get_queryset(self):
        return ExternalConnection.objects.filter(owner=self.request.user)

    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        connection = self.get_object()
        if not connection.enabled:
            raise ValidationError({"detail": "Подключение отключено"})
        try:
            if connection.kind == ExternalConnection.Kind.WORDPRESS:
                metadata = check_wordpress(connection)
            else:
                raise ValidationError({"detail": "Тип подключения не поддерживается"})
        except ValidationError as exc:
            connection.health_state = ExternalConnection.Health.DEGRADED
            connection.last_error = str(exc.detail if hasattr(exc, "detail") else exc)[:240]
            connection.last_checked_at = timezone.now()
            connection.save(update_fields=["health_state", "last_error", "last_checked_at", "updated_at"])
            raise
        connection.health_state = ExternalConnection.Health.HEALTHY
        connection.last_error = ""
        connection.last_checked_at = timezone.now()
        connection.metadata = {**(connection.metadata or {}), **metadata}
        connection.save(update_fields=["health_state", "last_error", "last_checked_at", "metadata", "updated_at"])
        return Response(self.get_serializer(connection).data)

    def perform_destroy(self, instance):
        instance.delete()


class AgentConnectionBindingViewSet(viewsets.ModelViewSet):
    serializer_class = AgentConnectionBindingSerializer

    def get_queryset(self):
        queryset = AgentConnectionBinding.objects.filter(agent__owner=self.request.user).select_related("agent", "connection")
        agent_id = str(self.request.query_params.get("agent") or "").strip()
        if agent_id:
            queryset = queryset.filter(agent_id=agent_id)
        return queryset

    @transaction.atomic
    def perform_create(self, serializer):
        binding = serializer.save()
        binding.full_clean()
        binding.save()
