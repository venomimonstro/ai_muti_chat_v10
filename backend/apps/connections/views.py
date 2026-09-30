from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.response import Response

from apps.agents.models import AgentRun

from .models import AgentConnectionBinding, ExternalConnection
from .serializers import AgentConnectionBindingSerializer, ExternalConnectionSerializer
from .vk import check_vk, exchange_code, oauth_authorize_url
from .wordpress import check_wordpress


ACTIVE_RUN_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}
VK_STATE_SALT = "ai-workspace-vk-oauth-v1"
VK_STATE_MAX_AGE_SECONDS = 10 * 60


def _api_validation(exc):
    if isinstance(exc, DjangoValidationError):
        return APIValidationError({"detail": exc.messages})
    return APIValidationError({"detail": str(exc)})


def _agent_has_active_run(agent_id):
    return AgentRun.objects.filter(
        Q(agent_id=agent_id) | Q(team__members__agent_id=agent_id, team__members__enabled=True),
        state__in=ACTIVE_RUN_STATES,
    ).exists()


def _connection_has_active_run(connection_id):
    return AgentRun.objects.filter(
        Q(agent__connection_bindings__connection_id=connection_id, agent__connection_bindings__enabled=True)
        | Q(
            team__members__agent__connection_bindings__connection_id=connection_id,
            team__members__agent__connection_bindings__enabled=True,
            team__members__enabled=True,
        ),
        state__in=ACTIVE_RUN_STATES,
    ).exists()


def _ensure_agent_idle(agent_id):
    if _agent_has_active_run(agent_id):
        raise APIValidationError({"detail": "Нельзя менять подключение агента во время активного запуска"})


def _ensure_connection_idle(connection_id):
    if _connection_has_active_run(connection_id):
        raise APIValidationError({"detail": "Подключение сейчас используется активным агентом или командой"})


def _vk_metadata(profile):
    return {"user_id": profile.user_id, "display_name": profile.display_name, "groups": profile.groups}


class ExternalConnectionViewSet(viewsets.ModelViewSet):
    serializer_class = ExternalConnectionSerializer

    def get_queryset(self):
        return ExternalConnection.objects.filter(owner=self.request.user)

    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        connection = self.get_object()
        if not connection.enabled:
            raise APIValidationError({"detail": "Подключение отключено"})
        try:
            if connection.kind == ExternalConnection.Kind.WORDPRESS:
                metadata = check_wordpress(connection)
            elif connection.kind == ExternalConnection.Kind.VK:
                metadata = _vk_metadata(check_vk(connection))
            else:
                raise DjangoValidationError("Тип подключения не поддерживается")
        except DjangoValidationError as exc:
            connection.health_state = ExternalConnection.Health.DEGRADED
            connection.last_error = " ".join(exc.messages)[:240]
            connection.last_checked_at = timezone.now()
            connection.save(update_fields=["health_state", "last_error", "last_checked_at", "updated_at"])
            raise _api_validation(exc) from exc
        connection.health_state = ExternalConnection.Health.HEALTHY
        connection.last_error = ""
        connection.last_checked_at = timezone.now()
        connection.metadata = {**(connection.metadata or {}), **metadata}
        connection.save(update_fields=["health_state", "last_error", "last_checked_at", "metadata", "updated_at"])
        return Response(self.get_serializer(connection).data)

    @action(detail=False, methods=["post"], url_path="vk-oauth-start")
    def vk_oauth_start(self, request):
        connection_id = str(request.data.get("connection") or "").strip()
        name = str(request.data.get("name") or "ВКонтакте").strip()[:160] or "ВКонтакте"
        if connection_id:
            connection = self.get_queryset().filter(pk=connection_id, kind=ExternalConnection.Kind.VK).first()
            if connection is None:
                raise APIValidationError({"connection": "VK-подключение не найдено"})
            _ensure_connection_idle(connection.id)
        else:
            connection = ExternalConnection.objects.create(
                owner=request.user,
                kind=ExternalConnection.Kind.VK,
                name=name,
                base_url="https://api.vk.com/method",
                enabled=True,
                health_state=ExternalConnection.Health.UNKNOWN,
            )
        state = signing.dumps(
            {"user_id": str(request.user.id), "connection_id": str(connection.id)},
            salt=VK_STATE_SALT,
            compress=True,
        )
        callback_url = request.build_absolute_uri(reverse("external-connection-vk-oauth-callback"))
        try:
            authorize_url = oauth_authorize_url(state=state, redirect_uri=callback_url)
        except DjangoValidationError as exc:
            if not connection.secret_encrypted and not connection.agent_bindings.exists():
                connection.delete()
            raise _api_validation(exc) from exc
        return Response({"connection": self.get_serializer(connection).data, "authorize_url": authorize_url})

    @action(detail=False, methods=["get"], url_path="vk-oauth-callback")
    def vk_oauth_callback(self, request):
        state = str(request.query_params.get("state") or "")
        code = str(request.query_params.get("code") or "")
        error = str(request.query_params.get("error") or "")
        if error:
            return redirect(f"/app/integrations?vk=error&reason={error[:80]}")
        if not state or not code:
            raise APIValidationError({"detail": "VK OAuth callback не содержит state/code"})
        try:
            payload = signing.loads(state, salt=VK_STATE_SALT, max_age=VK_STATE_MAX_AGE_SECONDS)
        except signing.BadSignature as exc:
            raise APIValidationError({"detail": "VK OAuth state недействителен или истёк"}) from exc
        if str(payload.get("user_id")) != str(request.user.id):
            raise APIValidationError({"detail": "VK OAuth принадлежит другому пользователю"})
        connection = self.get_queryset().filter(pk=payload.get("connection_id"), kind=ExternalConnection.Kind.VK).first()
        if connection is None:
            raise APIValidationError({"detail": "VK-подключение не найдено"})
        _ensure_connection_idle(connection.id)
        callback_url = request.build_absolute_uri(reverse("external-connection-vk-oauth-callback"))
        try:
            token = exchange_code(code=code, redirect_uri=callback_url)
        except DjangoValidationError as exc:
            connection.health_state = ExternalConnection.Health.DEGRADED
            connection.last_error = " ".join(exc.messages)[:240]
            connection.last_checked_at = timezone.now()
            connection.save(update_fields=["health_state", "last_error", "last_checked_at", "updated_at"])
            return redirect(f"/app/integrations?vk=degraded&connection={connection.id}")
        connection.set_secret(token["access_token"])
        connection.username = token.get("user_id") or connection.username
        connection.metadata = {
            **(connection.metadata or {}),
            "user_id": token.get("user_id") or "",
            "oauth_expires_in": token.get("expires_in"),
        }
        try:
            profile = check_vk(connection)
            connection.metadata = {**connection.metadata, **_vk_metadata(profile)}
            connection.health_state = ExternalConnection.Health.HEALTHY
            connection.last_error = ""
        except DjangoValidationError as exc:
            connection.health_state = ExternalConnection.Health.DEGRADED
            connection.last_error = " ".join(exc.messages)[:240]
        connection.last_checked_at = timezone.now()
        connection.save()
        connection_status = "connected" if connection.health_state == ExternalConnection.Health.HEALTHY else "degraded"
        return redirect(f"/app/integrations?vk={connection_status}&connection={connection.id}")

    @action(detail=True, methods=["post"], url_path="vk-select-group")
    def vk_select_group(self, request, pk=None):
        connection = self.get_object()
        if connection.kind != ExternalConnection.Kind.VK:
            raise APIValidationError({"detail": "Это не VK-подключение"})
        if connection.health_state != ExternalConnection.Health.HEALTHY:
            raise APIValidationError({"detail": "Сначала авторизуйте и проверьте VK"})
        group_id = str(request.data.get("group_id") or "").strip().lstrip("-")
        groups = [item for item in ((connection.metadata or {}).get("groups") or []) if isinstance(item, dict)]
        selected = next((item for item in groups if str(item.get("id") or "") == group_id), None)
        if selected is None:
            raise APIValidationError({"group_id": "Выберите доступное сообщество из списка VK"})
        _ensure_connection_idle(connection.id)
        connection.metadata = {
            **(connection.metadata or {}),
            "selected_group_id": group_id,
            "selected_group_name": str(selected.get("name") or "")[:160],
            "selected_group_screen_name": str(selected.get("screen_name") or "")[:160],
            "selected_group_photo": str(selected.get("photo_100") or "")[:500],
        }
        connection.save(update_fields=["metadata", "updated_at"])
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
