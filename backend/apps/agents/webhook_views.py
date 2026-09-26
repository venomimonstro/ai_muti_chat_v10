import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent, AgentTeam
from .webhook_models import AgentWebhookDelivery, AgentWebhookTrigger
from .webhook_tasks import dispatch_agent_webhook_delivery


MAX_WEBHOOK_BODY_BYTES = 64 * 1024


class AgentWebhookTriggerSerializer(serializers.ModelSerializer):
    subject_name = serializers.SerializerMethodField()
    subject_type = serializers.SerializerMethodField()
    endpoint = serializers.SerializerMethodField()

    class Meta:
        model = AgentWebhookTrigger
        fields = [
            "id", "agent", "team", "subject_name", "subject_type", "name", "objective",
            "enabled", "endpoint", "last_used_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "subject_name", "subject_type", "endpoint", "last_used_at", "created_at", "updated_at"]

    def get_subject_name(self, obj):
        subject = obj.agent or obj.team
        return subject.name if subject else ""

    def get_subject_type(self, obj):
        return "agent" if obj.agent_id else "team"

    def get_endpoint(self, obj):
        return f"/api/v1/agent-webhooks/{obj.id}/invoke/"

    def validate(self, attrs):
        request = self.context["request"]
        agent = attrs.get("agent") if "agent" in attrs else getattr(self.instance, "agent", None)
        team = attrs.get("team") if "team" in attrs else getattr(self.instance, "team", None)
        if bool(agent) == bool(team):
            raise serializers.ValidationError("Выберите либо одного сотрудника, либо одну команду")
        if agent and agent.owner_id != request.user.id:
            raise serializers.ValidationError({"agent": "Сотрудник недоступен"})
        if team and team.owner_id != request.user.id:
            raise serializers.ValidationError({"team": "Команда недоступна"})
        return attrs


class AgentWebhookTriggerViewSet(viewsets.ModelViewSet):
    serializer_class = AgentWebhookTriggerSerializer

    def get_queryset(self):
        return AgentWebhookTrigger.objects.filter(owner=self.request.user).select_related("agent", "team")

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_secret = secrets.token_urlsafe(32)
        trigger = serializer.save(owner=request.user, secret_hash=make_password(raw_secret))
        data = dict(self.get_serializer(trigger).data)
        data["secret"] = raw_secret
        data["secret_notice"] = "Секрет показывается только один раз. Сохраните его в системе-отправителе."
        return Response(data, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        trigger = serializer.save()
        return Response(self.get_serializer(trigger).data)

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @transaction.atomic
    def rotate_secret(self, request, pk=None):
        trigger = AgentWebhookTrigger.objects.select_for_update().get(pk=pk, owner=request.user)
        raw_secret = secrets.token_urlsafe(32)
        trigger.secret_hash = make_password(raw_secret)
        trigger.save(update_fields=["secret_hash", "updated_at"])
        data = dict(self.get_serializer(trigger).data)
        data["secret"] = raw_secret
        data["secret_notice"] = "Старый секрет больше не действует. Новый показывается только один раз."
        return Response(data)


class AgentWebhookInvokeView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, trigger_id):
        content_length = request.META.get("CONTENT_LENGTH")
        if content_length:
            try:
                if int(content_length) > MAX_WEBHOOK_BODY_BYTES:
                    return Response({"detail": "Webhook payload слишком большой"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
            except ValueError:
                pass

        trigger = AgentWebhookTrigger.objects.select_related("agent", "team").filter(pk=trigger_id, enabled=True).first()
        if trigger is None:
            return Response({"detail": "Webhook не найден"}, status=status.HTTP_404_NOT_FOUND)

        raw_secret = str(request.headers.get("X-Agent-Webhook-Secret") or "")
        if not raw_secret or not check_password(raw_secret, trigger.secret_hash):
            return Response({"detail": "Неверный webhook secret"}, status=status.HTTP_401_UNAUTHORIZED)

        event_id = str(request.headers.get("Idempotency-Key") or request.headers.get("X-Event-ID") or "").strip()
        if not event_id:
            return Response(
                {"detail": "Передайте уникальный Idempotency-Key или X-Event-ID, чтобы исключить двойной запуск"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(event_id) > 160:
            return Response({"detail": "Event ID слишком длинный"}, status=status.HTTP_400_BAD_REQUEST)

        payload = request.data
        if not isinstance(payload, dict):
            return Response({"detail": "Webhook payload должен быть JSON-объектом"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            try:
                delivery, created = AgentWebhookDelivery.objects.get_or_create(
                    trigger=trigger,
                    event_id=event_id,
                    defaults={"payload": payload},
                )
            except IntegrityError:
                delivery = AgentWebhookDelivery.objects.get(trigger=trigger, event_id=event_id)
                created = False

        if created:
            try:
                dispatch_agent_webhook_delivery.delay(str(delivery.id))
            except Exception as exc:
                AgentWebhookDelivery.objects.filter(pk=delivery.id, state=AgentWebhookDelivery.State.PENDING).update(
                    state=AgentWebhookDelivery.State.FAILED,
                    error_message=f"Очередь временно недоступна: {str(exc)[:1000]}",
                    updated_at=timezone.now(),
                )
                return Response({"detail": "Очередь автономных задач временно недоступна"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        delivery.refresh_from_db()
        return Response(
            {
                "delivery_id": str(delivery.id),
                "event_id": delivery.event_id,
                "state": delivery.state,
                "run_id": str(delivery.run_id) if delivery.run_id else None,
                "duplicate": not created,
            },
            status=status.HTTP_202_ACCEPTED,
        )
