import logging
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.handlers.asgi import ASGIRequest
from django.db.models import Q
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_registry.models import AIModel
from apps.billing.models import BalanceReservation

from .asgi_stream import follow_generation_async, managed_run_async
from .cost_preview import chat_cost_preview
from .managed_stream import managed_run
from .models import Conversation, Generation
from .preflight_terminal import classify_preflight_exception
from .product_identity import (
    create_identity_generation,
    direct_identity_answer,
    identity_preview,
    identity_sse,
)
from .serializers import SendMessageSerializer
from .streaming import _validate_replayed_generation, prepare

logger = logging.getLogger(__name__)
COST_CONFIRMATION_CHANGED = "cost_confirmation_changed"


SAFE_PREFLIGHT_DETAIL = (
    "Не удалось безопасно подготовить запрос. Деньги не списаны. "
    "Повторите запрос; если ошибка сохранится, система диагностики уже содержит техническую причину."
)


def _safe_preflight_response(exc):
    return Response(
        {
            "code": "preflight_failed",
            "support_code": classify_preflight_exception(exc),
            "detail": SAFE_PREFLIGHT_DETAIL,
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


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
        "spend_guard": {
            key: (str(item) if isinstance(item, Decimal) else item)
            for key, item in value.get("spend_guard", {}).items()
        },
        "blocked_by_spend_guard": value.get("blocked_by_spend_guard", False),
        "spend_guard_message": value.get("spend_guard_message", ""),
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


def _confirmation_payload(*, generation, required_max, detail):
    return {
        "code": COST_CONFIRMATION_CHANGED,
        "detail": detail,
        "estimated_min_rub": "0",
        "estimated_max_rub": str(required_max),
        "confirmation_required": True,
        "confirmation_threshold_rub": str(required_max),
        "selected_model": generation.routed_model or generation.model,
        "models": [],
        "spend_guard": {},
        "blocked_by_spend_guard": False,
        "spend_guard_message": "",
    }


def _held_confirmation_response(request, generation):
    """Resume or re-confirm a durable pre-provider Generation.

    ``prepare()`` has already built the exact context, candidate route and customer /
    provider reservations. Re-running preflight under the same idempotency key would
    either duplicate messages or replay the old FAILED state. Keeping the original
    QUEUED Generation is both safer and cheaper: after confirmation the exact same
    request continues once, with no second reserve and no second user message.
    """
    if generation.state != Generation.State.QUEUED or generation.error_code != COST_CONFIRMATION_CHANGED:
        return None
    reservation = (
        BalanceReservation.objects.filter(pk=generation.reservation_id)
        .only("amount_rub", "state")
        .first()
        if generation.reservation_id
        else None
    )
    if reservation is None or reservation.state != BalanceReservation.State.ACTIVE:
        generation.state = Generation.State.FAILED
        generation.error_code = "cost_confirmation_expired"
        generation.save(update_fields=["state", "error_code"])
        return Response(
            {
                "code": "cost_confirmation_expired",
                "detail": "Резерв подтверждения уже закрыт. Отправьте сообщение ещё раз — деньги повторно не списывались.",
            },
            status=status.HTTP_409_CONFLICT,
        )

    confirmed = request.data.get("confirm_cost") is True
    ceiling = _confirmed_ceiling(request) if confirmed else None
    required = reservation.amount_rub
    if not confirmed or ceiling is None or ceiling < required:
        return Response(
            _confirmation_payload(
                generation=generation,
                required_max=required,
                detail=(
                    "Фактический preflight требует подтверждения новой максимальной суммы. "
                    "Сумма только зарезервирована и не списана."
                ),
            ),
            status=status.HTTP_409_CONFLICT,
        )

    Generation.objects.filter(
        pk=generation.pk,
        state=Generation.State.QUEUED,
        error_code=COST_CONFIRMATION_CHANGED,
    ).update(error_code="")
    generation.error_code = ""
    return _stream_response(_customer_stream(request, generation, created=False))


def _customer_stream(request, generation, *, created):
    """Use the iterator type required by the active deployment protocol.

    Production is ASGI/Uvicorn, where StreamingHttpResponse must receive an async
    iterator. A reconnect to an already-running idempotent generation becomes a
    read-only follower instead of starting or charging another provider request.
    The synchronous path is retained for WSGI/test clients.
    """
    raw_request = getattr(request, "_request", None)
    if isinstance(raw_request, ASGIRequest):
        if not created and generation.state != Generation.State.QUEUED:
            return follow_generation_async(generation)
        return managed_run_async(generation)
    return managed_run(generation)


def _stream_response(iterator):
    response = StreamingHttpResponse(
        iterator,
        content_type="text/event-stream; charset=utf-8",
    )
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


def _existing_generation(user, idempotency_key):
    return (
        Generation.objects.filter(owner=user, idempotency_key=idempotency_key)
        .select_related("assistant_message", "user_message")
        .first()
    )


class ChatCostPreviewView(APIView):
    def post(self, request, conversation_id):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = _conversation(request.user, conversation_id)
        content = serializer.validated_data["content"]
        file_ids = serializer.validated_data.get("file_ids") or []
        if direct_identity_answer(content, file_ids) is not None:
            return Response(_serialize_preview(identity_preview(conversation)))
        try:
            value = chat_cost_preview(
                user=request.user,
                conversation=conversation,
                content=content,
                file_ids=file_ids,
            )
        except (ValidationError, AIModel.DoesNotExist) as exc:
            raise APIValidationError({"detail": getattr(exc, "messages", [str(exc)])}) from exc
        except Exception as exc:
            logger.exception(
                "Chat cost preview failed owner_id=%s conversation_id=%s",
                getattr(request.user, "pk", None),
                conversation_id,
            )
            return _safe_preflight_response(exc)
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
        content = serializer.validated_data["content"]
        client_message_id = serializer.validated_data["client_message_id"]
        file_ids = serializer.validated_data.get("file_ids") or []

        existing = _existing_generation(request.user, key)
        if existing is not None:
            try:
                _validate_replayed_generation(
                    existing,
                    conversation,
                    content,
                    client_message_id,
                    file_ids,
                )
            except ValidationError as exc:
                return Response(
                    {"detail": getattr(exc, "messages", [str(exc)])},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            held = _held_confirmation_response(request, existing)
            if held is not None:
                return held
            return _stream_response(
                _customer_stream(request, existing, created=False)
            )

        identity_answer = direct_identity_answer(content, file_ids)
        if identity_answer is not None:
            try:
                generation, _created = create_identity_generation(
                    user=request.user,
                    conversation=conversation,
                    content=content,
                    client_message_id=client_message_id,
                    idempotency_key=key,
                    answer=identity_answer,
                )
            except ValidationError as exc:
                return Response(
                    {"detail": getattr(exc, "messages", [str(exc)])},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return _stream_response(identity_sse(generation))

        try:
            preview = chat_cost_preview(
                user=request.user,
                conversation=conversation,
                content=content,
                file_ids=file_ids,
            )
        except (ValidationError, AIModel.DoesNotExist) as exc:
            raise APIValidationError({"detail": getattr(exc, "messages", [str(exc)])}) from exc
        except Exception as exc:
            logger.exception(
                "Chat stream preview failed owner_id=%s conversation_id=%s",
                getattr(request.user, "pk", None),
                conversation_id,
            )
            return _safe_preflight_response(exc)

        if preview.get("blocked_by_spend_guard"):
            payload = _serialize_preview(preview)
            payload.update(
                {
                    "code": "spend_safety_limit",
                    "detail": preview.get("spend_guard_message") or (
                        "Запрос превышает безопасный лимит расходов. Деньги не списаны."
                    ),
                }
            )
            return Response(payload, status=status.HTTP_409_CONFLICT)

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
        except Exception as exc:
            logger.exception(
                "Chat prepare failed owner_id=%s conversation_id=%s",
                getattr(request.user, "pk", None),
                conversation_id,
            )
            # preflight_terminal already persists a terminal Generation whenever
            # the transaction got far enough to create one. In production ASGI,
            # follow that durable row immediately so the same request receives the
            # normal safe terminal SSE instead of a raw 500 + reconnect cycle.
            failed_generation = _existing_generation(request.user, key)
            raw_request = getattr(request, "_request", None)
            if failed_generation is not None and isinstance(raw_request, ASGIRequest):
                return _stream_response(follow_generation_async(failed_generation))
            return _safe_preflight_response(exc)

        if created and generation.reservation_id:
            reservation = BalanceReservation.objects.only("amount_rub").get(pk=generation.reservation_id)
            threshold = Decimal(str(preview["confirmation_threshold_rub"]))
            requires_real_confirmation = reservation.amount_rub >= threshold
            allowed = ceiling if confirmed else None
            if requires_real_confirmation and (allowed is None or reservation.amount_rub > allowed):
                actual_max = reservation.amount_rub
                generation.error_code = COST_CONFIRMATION_CHANGED
                generation.save(update_fields=["error_code"])
                payload = _serialize_preview(preview)
                payload.update(
                    {
                        "code": COST_CONFIRMATION_CHANGED,
                        "detail": (
                            "Фактический preflight оказался дороже предварительной оценки. "
                            "Новая сумма только зарезервирована; подтвердите её, и тот же запрос продолжит работу."
                        ),
                        "estimated_max_rub": str(actual_max),
                        "confirmation_required": True,
                    }
                )
                return Response(payload, status=status.HTTP_409_CONFLICT)

        return _stream_response(
            _customer_stream(request, generation, created=created)
        )
