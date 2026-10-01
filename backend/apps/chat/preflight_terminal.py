from __future__ import annotations

import logging
import sys

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.billing.models import BalanceReservation
from apps.billing.services import release

from .models import Generation, Message

logger = logging.getLogger(__name__)


def _validation_text(exc: ValidationError) -> str:
    messages = getattr(exc, "messages", None)
    if messages:
        return " ".join(str(item) for item in messages).strip()
    return str(exc).strip()


def classify_preflight_exception(exc: Exception) -> str:
    """Map pre-provider failures to stable operational categories.

    These codes are intentionally coarse. They are safe to expose in diagnostics
    without persisting provider secrets or arbitrary internal exception strings.
    """
    if not isinstance(exc, ValidationError):
        return "preflight_internal"

    text = _validation_text(exc).casefold()
    if any(marker in text for marker in ("api-баланс", "закуп", "procurement", "funding", "provider balance")):
        return "preflight_provider_funding"
    if any(marker in text for marker in ("недостаточно средств", "кошел", "баланс пользователя", "wallet")):
        return "preflight_balance"
    if any(marker in text for marker in ("марж", "pricing", "price", "цена", "тариф")):
        return "preflight_price"
    if any(marker in text for marker in ("контекст", "context", "token", "токен", "окно модели")):
        return "preflight_context"
    if any(marker in text for marker in ("spend", "лимит расход", "лимит бюджета", "budget guard")):
        return "preflight_spend_guard"
    if any(marker in text for marker in ("web", "поиск", "актуальн")):
        return "preflight_web"
    if any(marker in text for marker in ("вложен", "изображ", "vision", "файл")):
        return "preflight_input"
    if any(marker in text for marker in ("модель", "маршрут", "candidate", "provider unavailable")):
        return "preflight_no_model"
    return "preflight_validation"


def _recover_preflight_reservation(generation):
    """Make an early customer reserve discoverable and release it exactly once.

    streaming.prepare() reserves with a deterministic ``generation:<uuid>`` key.
    A failure can happen before the reservation id is copied onto Generation. If
    the first best-effort release also failed, stale recovery would otherwise have
    no direct link to those frozen funds. Persist the link first, then retry the
    idempotent release. A later recovery sweep can finish the work if the database
    itself is temporarily unavailable now.
    """
    reservation = BalanceReservation.objects.filter(
        idempotency_key=f"generation:{generation.id}",
        state=BalanceReservation.State.ACTIVE,
    ).first()
    if reservation is None:
        return
    if generation.reservation_id != reservation.id:
        Generation.objects.filter(pk=generation.pk).update(reservation_id=reservation.id)
        generation.reservation_id = reservation.id
    try:
        release(reservation.id)
    except Exception:
        logger.exception(
            "Chat preflight reservation recovery failed generation_id=%s reservation_id=%s",
            generation.id,
            reservation.id,
        )


def _terminalize_matching_generation(*, user, content, client_message_id, idempotency_key, error_code):
    """Close only the request that just failed; never corrupt an unrelated replay."""
    generation = (
        Generation.objects.select_related("user_message", "assistant_message")
        .filter(owner=user, idempotency_key=idempotency_key)
        .first()
    )
    if generation is None:
        return None
    if str(generation.user_message.client_message_id or "") != str(client_message_id or ""):
        return generation
    if generation.user_message.content != content:
        return generation
    if generation.state not in {Generation.State.QUEUED, Generation.State.FAILED}:
        return generation

    _recover_preflight_reservation(generation)
    now = timezone.now()
    Generation.objects.filter(
        pk=generation.pk,
        state__in=[Generation.State.QUEUED, Generation.State.FAILED],
    ).update(
        state=Generation.State.FAILED,
        error_code=error_code,
        completed_at=now,
    )
    Message.objects.filter(
        pk=generation.assistant_message_id,
        status__in=[Message.Status.SAVED, Message.Status.STREAMING],
    ).update(status=Message.Status.FAILED)
    return generation


def install(streaming_module) -> None:
    """Guarantee consistent terminal state for every prepare() failure.

    The base streaming runtime creates durable user/assistant messages before some
    memory/routing/billing preflight work. If that work crashes, the wrapper makes
    sure the durable Generation and assistant Message cannot remain looking queued
    or saved forever. Existing idempotent generations are protected by matching the
    original client turn before any state change.
    """
    raw_prepare = streaming_module.prepare
    if getattr(raw_prepare, "_ai_workspace_preflight_terminal", False):
        return

    def prepare(*, user, conversation, content, client_message_id, idempotency_key, file_ids=None):
        try:
            return raw_prepare(
                user=user,
                conversation=conversation,
                content=content,
                client_message_id=client_message_id,
                idempotency_key=idempotency_key,
                file_ids=file_ids,
            )
        except Exception as exc:
            code = classify_preflight_exception(exc)
            try:
                _terminalize_matching_generation(
                    user=user,
                    content=content,
                    client_message_id=client_message_id,
                    idempotency_key=idempotency_key,
                    error_code=code,
                )
            except Exception:
                logger.exception(
                    "Chat preflight terminalization failed owner_id=%s idempotency_key=%s",
                    getattr(user, "pk", None),
                    idempotency_key,
                )
            raise

    prepare._ai_workspace_preflight_terminal = True
    prepare._raw_prepare = raw_prepare
    streaming_module.prepare = prepare

    for module_name in ("apps.chat.services", "apps.chat.views"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "prepare"):
            module.prepare = prepare
