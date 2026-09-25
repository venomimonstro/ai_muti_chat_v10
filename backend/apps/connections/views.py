from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.agents.models import AgentRun

from .models import AgentConnectionBinding, ExternalConnection
from .serializers import AgentConnectionBindingSerializer, ExternalConnectionSerializer
from .wordpress import check_wordpress


ACTIVE_RUN_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}


def _agent_has_active_run(agent_id):
    return AgentRun.objects.filter(
        Q(agent_id=agent_id) | Q(team__members__agent_id=agent_id, team__members__enabled=True),
        state__in=ACTIVE_RUN_STATES,
    ).exists()


def _connection_has_active_run(connection_id):
    return AgentRun.objects.filter(
        Q(
            agent__connection_bindings__connection_id=connection_id,
            agent__connection_bindings__enabled=True,
        )
        | Q(
            team__members__agent__connection_bindings__connection_id=connection_id,
            team__members__agent__connection_bindings__enabled=True,
            team__members__enabled=True,
        ),
        state__in=ACTIVE_RUN_STATES,
    ).exists()


def _ensure_agent_idle(agent_id):
    if _agent_has_active_run(agent_id):
        raise ValidationError({"detail": "Нельзя менять подключение агента во время активного запуска"})


def _ensure_connection_idle(connection_id):
    if _connection_has_active_run(connection_id):
        raise ValidationError({"detail": "Подключение сейчас используется активным агентом или командой"})


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

    def perform_update(self, serializer):
        _ensure_connection_idle(serializer.instance.id)
        serializer.save()

    def perform_destroy(self, instance):
        _ensure_connection_idle(instance.id)
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
        agent = serializer.validated_data["agent"]
        _ensure_agent_idle(agent.id)
        binding = serializer.save()
        binding.full_clean()
        binding.save()

    @transaction.atomic
    def perform_update(self, serializer):
        current = serializer.instance
        next_agent = serializer.validated_data.get("agent", current.agent)
        _ensure_agent_idle(current.agent_id)
        if next_agent.id != current.agent_id:
            _ensure_agent_idle(next_agent.id)
        binding = serializer.save()
        binding.full_clean()
        binding.save()

    @transaction.atomic
    def perform_destroy(self, instance):
        _ensure_agent_idle(instance.agent_id)
        instance.delete()
