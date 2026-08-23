import hashlib
import json
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.billing.services import credit, debit_paid

from .models import (
    Payment,
    PaymentCostSnapshot,
    PaymentEvent,
    ReconciliationRun,
    Refund,
)
from .provider import YooKassaClient

CENT = Decimal("0.01")


def _money(value):
    return Decimal(str(value)).quantize(CENT)


def _assert_payments_ready():
    if not settings.PAYMENTS_ENABLED:
        raise ValidationError("Пополнение баланса временно отключено")
    if settings.PAYMENTS_LIVE_ENABLED and settings.PAYMENTS_FISCALIZATION_MODE == "disabled":
        raise ValidationError("Боевые платежи заблокированы до настройки фискализации")


def _receipt(user, amount):
    if settings.PAYMENTS_FISCALIZATION_MODE != "provider_receipt":
        return None
    if not user.email:
        raise ValidationError("Для формирования чека укажите email")
    return {
        "customer": {"email": user.email},
        "items": [
            {
                "description": "Пополнение баланса AI Workspace",
                "quantity": "1.00",
                "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
                "vat_code": settings.PAYMENTS_VAT_CODE,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }
        ],
    }


def create_topup(*, user, amount, idempotency_key, client=None):
    _assert_payments_ready()
    amount = _money(amount)
    if not 1 <= len(idempotency_key) <= 64:
        raise ValidationError("Idempotency-Key должен содержать от 1 до 64 символов")
    if amount < Decimal(settings.PAYMENT_MIN_RUB) or amount > Decimal(settings.PAYMENT_MAX_RUB):
        raise ValidationError("Сумма пополнения вне разрешённого диапазона")

    payment, _ = Payment.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "user": user,
            "amount_rub": amount,
            "return_url": settings.PAYMENT_RETURN_URL,
            "description": f"Пополнение баланса {amount:.2f} ₽",
            "receipt_status": (
                Payment.ReceiptStatus.PENDING
                if settings.PAYMENTS_FISCALIZATION_MODE == "provider_receipt"
                else Payment.ReceiptStatus.LEGAL_REVIEW
            ),
        },
    )
    if payment.user_id != user.id or payment.amount_rub != amount:
        raise ValidationError("Idempotency-Key уже использован для другой операции")
    if payment.provider_payment_id:
        return payment

    payload = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": settings.PAYMENT_RETURN_URL},
        "description": payment.description,
        "metadata": {"payment_id": str(payment.id), "user_id": str(user.id)},
    }
    receipt = _receipt(user, amount)
    if receipt:
        payload["receipt"] = receipt
    client = client or YooKassaClient.from_settings()
    try:
        remote = client.create_payment(payload, idempotency_key)
    except Exception as exc:
        payment.last_error = exc.__class__.__name__
        payment.save(update_fields=["last_error", "updated_at"])
        raise
    payment.provider_payment_id = remote["id"]
    payment.status = remote.get("status", Payment.Status.PENDING)
    payment.confirmation_url = (remote.get("confirmation") or {}).get("confirmation_url", "")
    payment.provider_payload = remote
    payment.last_error = ""
    payment.save(
        update_fields=[
            "provider_payment_id",
            "status",
            "confirmation_url",
            "provider_payload",
            "last_error",
            "updated_at",
        ]
    )
    return payment


def _validate_remote_payment(payment, remote):
    amount = remote.get("amount") or {}
    metadata = remote.get("metadata") or {}
    if remote.get("id") != payment.provider_payment_id:
        raise ValidationError("Provider payment id mismatch")
    if _money(amount.get("value")) != payment.amount_rub or amount.get("currency") != "RUB":
        raise ValidationError("Provider payment amount mismatch")
    if metadata.get("payment_id") != str(payment.id):
        raise ValidationError("Provider payment metadata mismatch")


@transaction.atomic
def apply_payment_status(payment_id, remote, *, event=None):
    payment = Payment.objects.select_for_update().select_related("user").get(id=payment_id)
    _validate_remote_payment(payment, remote)
    remote_status = remote.get("status")
    result = "ignored"
    if remote_status == Payment.Status.SUCCEEDED:
        if payment.credited_at is None:
            credit(
                payment.user,
                payment.amount_rub,
                "payment",
                payment.id,
                bucket="paid",
            )
            payment.credited_at = timezone.now()
            result = "credited"
        payment.status = Payment.Status.SUCCEEDED
        income = remote.get("income_amount") or {}
        net = _money(income["value"]) if income.get("value") else None
        PaymentCostSnapshot.objects.get_or_create(
            payment=payment,
            defaults={
                "gross_rub": payment.amount_rub,
                "net_received_rub": net,
                "acquiring_fee_rub": payment.amount_rub - net if net is not None else None,
                "source": "provider_actual" if net is not None else "provider_pending",
            },
        )
    elif remote_status == Payment.Status.CANCELED and payment.status != Payment.Status.SUCCEEDED:
        payment.status = Payment.Status.CANCELED
        result = "canceled"
    elif remote_status == Payment.Status.PENDING and payment.status == Payment.Status.CREATED:
        payment.status = Payment.Status.PENDING
        result = "pending"
    payment.provider_payload = remote
    payment.save(update_fields=["status", "credited_at", "provider_payload", "updated_at"])
    if event:
        event.result = result
        event.processed_at = timezone.now()
        event.save(update_fields=["result", "processed_at"])
    return result


def process_webhook(payload, *, client=None):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    object_data = payload.get("object") or {}
    event, _ = PaymentEvent.objects.get_or_create(
        payload_hash=digest,
        defaults={
            "event_name": payload.get("event", ""),
            "object_id": object_data.get("id", ""),
            "payload": payload,
        },
    )
    if event.processed_at:
        return event.result
    if payload.get("type") != "notification":
        raise ValidationError("Invalid notification type")
    client = client or YooKassaClient.from_settings()
    if payload.get("event", "").startswith("payment."):
        payment = Payment.objects.filter(provider_payment_id=object_data.get("id")).first()
        if not payment:
            event.result = "unknown_payment"
            event.processed_at = timezone.now()
            event.save(update_fields=["result", "processed_at"])
            return event.result
        remote = client.get_payment(payment.provider_payment_id)
        return apply_payment_status(payment.id, remote, event=event)
    if payload.get("event") == "refund.succeeded":
        refund = Refund.objects.filter(provider_refund_id=object_data.get("id")).first()
        if not refund:
            event.result = "unknown_refund"
            event.processed_at = timezone.now()
            event.save(update_fields=["result", "processed_at"])
            return event.result
        remote = client.get_refund(refund.provider_refund_id)
        return apply_refund_status(refund.id, remote, event=event)
    event.result = "unsupported_event"
    event.processed_at = timezone.now()
    event.save(update_fields=["result", "processed_at"])
    return event.result


def create_refund(*, payment, amount, idempotency_key, client=None):
    _assert_payments_ready()
    amount = _money(amount)
    payment = Payment.objects.select_related("user").get(pk=payment.pk)
    if not 1 <= len(idempotency_key) <= 64:
        raise ValidationError("Idempotency-Key должен содержать от 1 до 64 символов")
    if payment.status != Payment.Status.SUCCEEDED:
        raise ValidationError("Возврат доступен только для успешного платежа")
    existing = Refund.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        if existing.payment_id != payment.id or existing.amount_rub != amount:
            raise ValidationError("Idempotency-Key уже использован для другой операции")
        if existing.provider_refund_id:
            return existing
    already = payment.refunds.filter(
        status__in=[Refund.Status.CREATED, Refund.Status.PENDING, Refund.Status.SUCCEEDED]
    ).aggregate(total=Sum("amount_rub"))["total"] or Decimal("0")
    if amount < Decimal("1.00") or already + amount > payment.amount_rub:
        raise ValidationError("Недопустимая сумма возврата")
    refund, _created = Refund.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={"payment": payment, "amount_rub": amount},
    )
    if refund.payment_id != payment.id or refund.amount_rub != amount:
        raise ValidationError("Idempotency-Key уже использован для другой операции")
    if refund.wallet_debited_at is None:
        debit_paid(payment.user, amount, "refund", refund.id)
        refund.wallet_debited_at = timezone.now()
        refund.save(update_fields=["wallet_debited_at", "updated_at"])
    client = client or YooKassaClient.from_settings()
    remote = client.create_refund(
        {
            "payment_id": payment.provider_payment_id,
            "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        },
        idempotency_key,
    )
    refund.provider_refund_id = remote["id"]
    refund.status = remote.get("status", Refund.Status.PENDING)
    refund.provider_payload = remote
    refund.save(update_fields=["provider_refund_id", "status", "provider_payload", "updated_at"])
    return refund


@transaction.atomic
def apply_refund_status(refund_id, remote, *, event=None):
    refund = Refund.objects.select_for_update().select_related("payment__user").get(id=refund_id)
    amount = remote.get("amount") or {}
    if remote.get("id") != refund.provider_refund_id:
        raise ValidationError("Provider refund id mismatch")
    if remote.get("payment_id") != refund.payment.provider_payment_id:
        raise ValidationError("Provider refund payment mismatch")
    if _money(amount.get("value")) != refund.amount_rub or amount.get("currency") != "RUB":
        raise ValidationError("Provider refund amount mismatch")
    status = remote.get("status")
    result = "ignored"
    if status == Refund.Status.SUCCEEDED:
        refund.status = Refund.Status.SUCCEEDED
        result = "refund_succeeded"
    elif status == Refund.Status.CANCELED and refund.status != Refund.Status.SUCCEEDED:
        refund.status = Refund.Status.CANCELED
        credit(refund.payment.user, refund.amount_rub, "refund_cancel", refund.id, bucket="paid")
        result = "refund_canceled_released"
    refund.provider_payload = remote
    refund.save(update_fields=["status", "provider_payload", "updated_at"])
    if event:
        event.result = result
        event.processed_at = timezone.now()
        event.save(update_fields=["result", "processed_at"])
    return result


def reconcile_open_payments(*, client=None):
    client = client or YooKassaClient.from_settings()
    run = ReconciliationRun.objects.create()
    for payment in Payment.objects.filter(
        status__in=[Payment.Status.CREATED, Payment.Status.PENDING]
    ):
        run.checked_count += 1
        try:
            remote = client.get_payment(payment.provider_payment_id)
            result = apply_payment_status(payment.id, remote)
            if result in {"credited", "canceled"}:
                run.corrected_count += 1
        except Exception:
            run.error_count += 1
    run.status = (
        ReconciliationRun.Status.FAILED if run.error_count else ReconciliationRun.Status.SUCCEEDED
    )
    run.finished_at = timezone.now()
    run.save(
        update_fields=["checked_count", "corrected_count", "error_count", "status", "finished_at"]
    )
    return run
