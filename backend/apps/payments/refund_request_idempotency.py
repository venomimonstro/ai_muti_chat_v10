from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import RefundRequest
from .services import create_refund_request

CENT = Decimal("0.01")
OPEN_STATUSES = {
    RefundRequest.Status.PENDING,
    RefundRequest.Status.APPROVED,
    RefundRequest.Status.PROCESSING,
}


def _amount(value):
    try:
        return Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Некорректная сумма возврата") from exc


@transaction.atomic
def create_refund_request_idempotent(*, user, payment, amount, reason, idempotency_key):
    key = str(idempotency_key or "").strip()
    if not 1 <= len(key) <= 160:
        raise ValidationError("Idempotency-Key обязателен для заявки на возврат")
    normalized_amount = _amount(amount)
    normalized_reason = str(reason or "")[:4000]

    # Serialize all refund-request creation for a user. This covers two tabs,
    # reverse-proxy retries and concurrent POSTs before the unique constraint is visible.
    user.__class__.objects.select_for_update().only("pk").get(pk=user.pk)
    existing = (
        RefundRequest.objects.select_related("payment")
        .filter(user=user, idempotency_key=key)
        .first()
    )
    if existing is not None:
        if (
            existing.payment_id != payment.id
            or existing.amount_rub != normalized_amount
            or existing.reason != normalized_reason
        ):
            raise ValidationError("Idempotency-Key уже использован для другой заявки")
        return existing

    # A lost browser response must not let the customer accidentally create a
    # second hold with another key/amount. One payment may have only one open
    # customer request; another partial request is allowed after it is resolved.
    open_request = (
        RefundRequest.objects.filter(
            user=user,
            payment=payment,
            status__in=OPEN_STATUSES,
        )
        .order_by("created_at")
        .first()
    )
    if open_request is not None:
        raise ValidationError(
            "По этому платежу уже есть заявка на возврат в обработке. "
            "Дождитесь её завершения перед новой заявкой."
        )

    request = create_refund_request(
        user=user,
        payment=payment,
        amount=normalized_amount,
        reason=normalized_reason,
    )
    request.idempotency_key = key
    request.save(update_fields=["idempotency_key", "updated_at"])
    return request
