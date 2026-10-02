import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.handlers.asgi import ASGIRequest
from django.db import close_old_connections, transaction
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

from .asgi_stream import follow_generation_async, managed_run_async
from .cost_preview import chat_cost_preview
from .durable_follow import follow_existing_generation
from .managed_stream import managed_run
from .models import Conversation, Generation, Message
from .paid_search_billing import public_search_charge
from .preflight_terminal import classify_preflight_exception
from .product_identity import (
    create_identity_generation,
    direct_identity_answer,
    identity_preview,
    identity_sse,
)
from .serializers import SendMessageSerializer
from .streaming import _validate_replayed_generation, prepare, sse

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
        "estimated_llm_max_rub": str(
            value.get("estimated_llm_max_rub", value["estimated_max_rub"])
        ),
        "estimated_search_max_rub": str(value.get("estimated_search_max_rub", 0)),
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
    search = public_search_charge(generation)
    return {
        "code": COST_CONFIRMATION_CHANGED,
        "detail": detail,
        "estimated_min_rub": "0",
        "estimated_max_rub": str(required_max + search),
        "estimated_llm_max_rub": str(required_max),
        "estimated_search_max_rub": str(search),
        "confirmation_required": True,
        "confirmation_threshold_rub": str(required_max + search),
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
    search_reservation = BalanceReservation.objects.filter(
        idempotency_key=f"web-search:{generation.id}",
        state=BalanceReservation.State.ACTIVE,
    ).only("amount_rub").first()
    search_amount = search_reservation.amount_rub if search_reservation is not None else Decimal("0")
    required_total = required + search_amount
    if not confirmed or ceiling is None or ceiling < required_total:
        return Response(
            {
                "code": COST_CONFIRMATION_CHANGED,
                "detail": (
                    "Фактический preflight требует подтверждения новой максимальной суммы. "
                    "Сумма только зарезервирована и не списана."
                ),
                "estimated_min_rub": "0",
                "estimated_max_rub": str(required_total),
                "estimated_llm_max_rub": str(required),
                "estimated_search_max_rub": str(search_amount),
                "confirmation_required": True,
                "confirmation_threshold_rub": str(required_total),
                "selected_model": generation.routed_model or generation.model,
                "models": [],
                "spend_guard": {},
                "blocked_by_spend_guard": False,
                "spend_guard_message": "",
            },
            status=status.HTTP_409_CONFLICT,
        )

    Generation.objects.filter(
        pk=generation.pk,
        state=Generation.State.QUEUED,
        error_code=COST_CONFIRMATION_CHANGED,
    ).update(error_code="")
    generation.error_code = ""
    try:
        _authorize_customer_stream(generation)
    except Exception as exc:
        logger.exception(
            "Chat held-confirmation authorization failed generation_id=%s",
            generation.id,
        )
        generation = _terminalize_stream_authorization_failure(generation, exc)
    return _stream_response(_customer_stream(request, generation, created=False))


@transaction.atomic
def _authorize_customer_stream(generation):
    # prepare() publishes QUEUED before routing/search/reservations finish. The
    # HTTP cost ceiling check must also complete before a reconnect may execute it.
    locked = Generation.objects.select_for_update().get(pk=generation.pk)
    if locked.state == Generation.State.QUEUED and not locked.error_code:
        context = dict(locked.context_snapshot or {})
        context["customer_stream_authorized"] = True
        locked.context_snapshot = context
        locked.save(update_fields=["context_snapshot"])
        generation.context_snapshot = context


def _terminalize_stream_authorization_failure(generation, exc):
    """Fail one prepared turn instead of leaving a forever-QUEUED reservation.

    This path runs strictly before provider execution. Marking the Generation
    terminal also triggers existing procurement/search cleanup signals.
    """
    try:
        with transaction.atomic():
            locked = (
                Generation.objects.select_for_update()
                .select_related("assistant_message")
                .get(pk=generation.pk)
            )
            if locked.state != Generation.State.QUEUED:
                generation.state = locked.state
                generation.error_code = locked.error_code
                return locked

            if locked.reservation_id:
                try:
                    release(locked.reservation_id)
                except Exception:
                    logger.exception(
                        "Chat stream authorization customer reserve release failed generation_id=%s",
                        locked.id,
                    )

            locked.state = Generation.State.FAILED
            locked.error_code = "stream_authorization_failed"
            locked.completed_at = timezone.now()
            locked.save(update_fields=["state", "error_code", "completed_at"])
            Message.objects.filter(pk=locked.assistant_message_id).update(
                status=Message.Status.FAILED
            )
            generation.state = locked.state
            generation.error_code = locked.error_code
            generation.completed_at = locked.completed_at
            return locked
    except Exception:
        logger.exception(
            "Chat stream authorization terminalization failed generation_id=%s original=%r",
            getattr(generation, "id", None),
            exc,
        )
        raise


def _preparing_snapshot(generation_id):
    close_old_connections()
    try:
        return Generation.objects.only("state", "error_code", "context_snapshot").get(pk=generation_id)
    finally:
        close_old_connections()


def _wait_for_authorized_stream(generation):
    yield sse("generation", {"id": str(generation.id), "state": generation.state, "reconnected": True})
    while True:
        current = _preparing_snapshot(generation.id)
        if current.state != Generation.State.QUEUED:
            yield from follow_existing_generation(current)
            return
        if current.error_code == COST_CONFIRMATION_CHANGED:
            # Close this read-only follower so the browser reopens the same key and
            # receives the normal HTTP 409 cost-confirmation contract.
            yield sse("heartbeat", {"state": "awaiting_confirmation"})
            return
        if (current.context_snapshot or {}).get("customer_stream_authorized"):
            yield from managed_run(current)
            return
        yield sse("heartbeat", {"state": "preparing"})
        time.sleep(0.5)


async def _wait_for_authorized_stream_async(generation):
    yield sse("generation", {"id": str(generation.id), "state": generation.state, "reconnected": True})
    while True:
        current = await asyncio.to_thread(_preparing_snapshot, generation.id)
        if current.state != Generation.State.QUEUED:
            async for chunk in follow_generation_async(current):
                yield chunk
            return
        if current.error_code == COST_CONFIRMATION_CHANGED:
            yield sse("heartbeat", {"state": "awaiting_confirmation"})
            return
        if (current.context_snapshot or {}).get("customer_stream_authorized"):
            async for chunk in managed_run_async(current):
                yield chunk
            return
        yield sse("heartbeat", {"state": "preparing"})
        await asyncio.sleep(0.5)


def _customer_stream(request, generation, *, created):
    """Use one durable producer/follower contract under ASGI and WSGI.

    A reconnect to an already-running or terminal idempotent Generation is always a
    read-only follower. Only a newly created / deliberately held QUEUED Generation
    may enter the provider runtime. This keeps production ASGI, tests and fallback
    WSGI deployments behaviorally identical and prevents replay from re-entering the
    provider/billing path.
    """
    raw_request = getattr(request, "_request", None)
    awaiting_authorization = (
        not created
        and generation.state == Generation.State.QUEUED
        and not (generation.context_snapshot or {}).get("customer_stream_authorized")
    )
    if awaiting_authorization:
        return _wait_for_authorized_stream_async(generation) if isinstance(raw_request, ASGIRequest) else _wait_for_authorized_stream(generation)
    follow = not created and generation.state != Generation.State.QUEUED
    if isinstance(raw_request, ASGIRequest):
        return follow_generation_async(generation) if follow else managed_run_async(generation)
    return follow_existing_generation(generation) if follow else managed_run(generation)


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


def _durable_prepare_failure(request, *, user, idempotency_key):
    """Attach to a Generation already created by prepare() instead of returning 400.

    Preflight can fail after durable messages/Generation were created. Returning a
    plain HTTP error would make the browser believe the turn was never accepted,
    restore the same prompt into the composer, and then also load the persisted failed
    turn from history. Following the durable terminal state gives one acceptance ack,
    one public error and one idempotent retry surface instead.
    """
    generation = _existing_generation(user, idempotency_key)
    if generation is None:
        return None
    return _stream_response(_customer_stream(request, generation, created=False))


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
            durable = _durable_prepare_failure(
                request,
                user=request.user,
                idempotency_key=key,
            )
            if durable is not None:
                return durable
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
            durable = _durable_prepare_failure(
                request,
                user=request.user,
                idempotency_key=key,
            )
            if durable is not None:
                return durable
            return _safe_preflight_response(exc)

        if created and generation.reservation_id:
            reservation = BalanceReservation.objects.only("amount_rub").get(pk=generation.reservation_id)
            search_reservation = BalanceReservation.objects.filter(
                idempotency_key=f"web-search:{generation.id}",
                state=BalanceReservation.State.ACTIVE,
            ).only("amount_rub").first()
            actual_max = reservation.amount_rub + (
                search_reservation.amount_rub if search_reservation is not None else Decimal("0")
            )
            threshold = Decimal(str(preview["confirmation_threshold_rub"]))
            requires_real_confirmation = actual_max >= threshold
            allowed = ceiling if confirmed else None
            if requires_real_confirmation and (allowed is None or actual_max > allowed):
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
                        "estimated_llm_max_rub": str(reservation.amount_rub),
                        "estimated_search_max_rub": str(
                            search_reservation.amount_rub if search_reservation is not None else Decimal("0")
                        ),
                        "confirmation_required": True,
                    }
                )
                return Response(payload, status=status.HTTP_409_CONFLICT)

        try:
            _authorize_customer_stream(generation)
        except Exception as exc:
            logger.exception(
                "Chat customer stream authorization failed generation_id=%s",
                generation.id,
            )
            generation = _terminalize_stream_authorization_failure(generation, exc)
            created = False
        return _stream_response(
            _customer_stream(request, generation, created=created)
        )
