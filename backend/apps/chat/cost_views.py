from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_registry.models import AIModel

from .cost_preview import chat_cost_preview
from .managed_stream import managed_run
from .models import Conversation
from .serializers import SendMessageSerializer
from .streaming import prepare


def _conversation(user, conversation_id):
    conversation = (
        Conversation.objects.filter(pk=conversation_id, owner=user)
        .filter(Q(ui_state__isnull=True) | Q(ui_state__deleted_at__isnull=True))
        .first()
    )
    if conversation is None:
        raise APIValidationError({"conversation": "Чат не найден или удалён"})
    return conversation


def _serialize_preview(value):
    return {
        "estimated_min_rub": str(value["estimated_min_rub"]),
        "estimated_max_rub": str(value["estimated_max_rub"]),
        "confirmation_required": value["confirmation_required"],
        "confirmation_threshold_rub": str(value["confirmation_threshold_rub"]),
        "selected_model": value["selected_model"],
        "models": value["models"],
    }


class ChatCostPreviewView(APIView):
    def post(self, request, conversation_id):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = _conversation(request.user, conversation_id)
        try:
            value = chat_cost_preview(
                user=request.user,
                conversation=conversation,
                content=serializer.validated_data["content"],
                file_ids=serializer.validated_data.get("file_ids") or [],
            )
        except (ValidationError, AIModel.DoesNotExist) as exc:
            raise APIValidationError({"detail": getattr(exc, "messages", [str(exc)])}) from exc
        return Response(_serialize_preview(value))


class ConfirmedConversationStreamView(APIView):
    def post(self, request, conversation_id):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key")
        if not key or len(key) > 160:
            return Response(
                {"detail": "Idempotency-Key обязателен"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        conversation = _conversation(request.user, conversation_id)
        try:
            preview = chat_cost_preview(
                user=request.user,
                conversation=conversation,
                content=serializer.validated_data["content"],
                file_ids=serializer.validated_data.get("file_ids") or [],
            )
        except (ValidationError, AIModel.DoesNotExist) as exc:
            raise APIValidationError({"detail": getattr(exc, "messages", [str(exc)])}) from exc
        if preview["confirmation_required"] and request.data.get("confirm_cost") is not True:
            payload = _serialize_preview(preview)
            payload.update(
                {
                    "code": "cost_confirmation_required",
                    "detail": "Подтвердите максимальную стоимость запроса до запуска модели",
                }
            )
            return Response(payload, status=status.HTTP_409_CONFLICT)
        try:
            generation, _created = prepare(
                user=request.user,
                conversation=conversation,
                idempotency_key=key,
                **serializer.validated_data,
            )
        except (ValidationError, AIModel.DoesNotExist) as exc:
            return Response(
                {"detail": getattr(exc, "messages", [str(exc)])},
                status=status.HTTP_400_BAD_REQUEST,
            )
        response = StreamingHttpResponse(
            managed_run(generation),
            content_type="text/event-stream; charset=utf-8",
        )
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response
