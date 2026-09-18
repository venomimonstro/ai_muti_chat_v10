from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_registry.models import AIModel
from apps.billing.models import BalanceReservation
from apps.billing.services import release

from .cost_preview import chat_cost_preview
from .managed_stream import managed_run
from .models import Conversation, Generation, Message
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


def _confirmed_ceiling(request):
    raw = request.data.get("confirmed_max_rub")
    if raw in {None, ""}:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise APIValidationError({"confirmed_max_rub": "Некорректная сумма подтверждения"}) from exc
    if value <= 0:
        raise APIValidationError({"confirmed_max_rub": "Сумма подтверждения должна быть положительной"})
    return value


def _fail_pre_provider_generation(generation, code):
    if generation.reservation_id:
        release(generation.reservation_id)
    assistant = generation.assistant_message
    assistant.status = Message.Status.FAILED
    assistant.save(update_fields=["status"])
    generation.state = Generation.State.FAILED
    generation.error_code = code
    generation.completed_at = timezone.now()
    generation.save(update_fields=["state", "error_code", "completed_at"])


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
        confirmed = request.data.get("confirm_cost") is True
        ceiling = _confirmed_ceiling(request) if confirmed else None
        if preview["confirmation_required"] and not confirmed:
            payload = _serialize_preview(preview)
            payload.update(
                {
                    "code": "cost_confirmation_required",
                    "detail": "Подтвердите максимальную стоимость запроса до запуска модели",
                }
            )
            return Response(payload, status=status.HTTP_409_CONFLICT)
        if confirmed and (ceiling is None or ceiling < preview["estimated_max_rub"]):
            payload = _serialize_preview(preview)
            payload.update(
                {
                    "code": "cost_confirmation_required",
                    "detail": "Расчётная стоимость изменилась. Подтвердите новый максимум.",
                }
            )
            return Response(payload, status=status.HTTP_409_CONFLICT)
        try:
            generation, created = prepare(
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

        if created and generation.reservation_id:
            reservation = BalanceReservation.objects.only("amount_rub").get(pk=generation.reservation_id)
            threshold = Decimal(str(preview["confirmation_threshold_rub"]))
            requires_real_confirmation = reservation.amount_rub >= threshold
            allowed = ceiling if confirmed else None
            if requires_real_confirmation and (allowed is None or reservation.amount_rub > allowed):
                actual_max = reservation.amount_rub
                _fail_pre_provider_generation(generation, "cost_confirmation_changed")
                payload = _serialize_preview(preview)
                payload.update(
                    {
                        "code": "cost_confirmation_changed",
                        "detail": (
                            "Фактический preflight оказался дороже предварительной оценки. "
                            "Деньги не списаны; подтвердите новую сумму и отправьте запрос ещё раз."
                        ),
                        "estimated_max_rub": str(actual_max),
                        "confirmation_required": True,
                    }
                )
                return Response(payload, status=status.HTTP_409_CONFLICT)

        response = StreamingHttpResponse(
            managed_run(generation),
            content_type="text/event-stream; charset=utf-8",
        )
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response
