import hashlib
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.models import Notification, User
from apps.b2b_api.models import APIKey, Organization
from apps.billing.services import debit_paid

from .models import Payment, Refund, RefundRequest


def _money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Invalid provider refund amount") from exc


def _notify_financial_incident(*, payment, refund_id, amount, detail=None):
    admins = User.objects.filter(
        role=User.Role.PLATFORM_ADMIN,
        status=User.Status.ACTIVE,
    ).only("id")
    for admin in admins.iterator():
        Notification.objects.get_or_create(
            user=admin,
            dedupe_key=f"external-refund-gap:{refund_id}",
            defaults={
                "title": "Внешний возврат требует финансовой проверки",
                "body": detail or (
                    f"ЮKassa вернула {amount:.2f} ₽ по платежу {payment.id}, "
                    "но свободного платного баланса клиента недостаточно. "
                    "Аккаунт и B2B-доступ заблокированы до ручной сверки."
                ),
                "level": Notification.Level.WARNING,
                "action_url": "/admin-console/payments",
            },
        )


def _freeze_user_spend(user):
    user.status = User.Status.BLOCKED
    user.save(update_fields=["status"])
    organization_ids = list(
        Organization.objects.filter(billing_user=user, active=True).values_list("id", flat=True)
    )
    Organization.objects.filter(id__in=organization_ids).update(active=False)
    APIKey.objects.filter(
        organization_id__in=organization_ids,
        revoked_at__isnull=True,
    ).update(revoked_at=timezone.now())


def _recover_timed_out_local_refund(*, payment, refund_id, amount, remote):
    """Attach a provider webhook to the one local refund whose POST timed out.

    create_refund() persists the wallet hold and local row before the network call.
    Therefore a webhook can arrive while provider_refund_id is still NULL. Matching
    that row first is mandatory: treating it as external would debit the wallet twice.
    """
    candidates = list(
        Refund.objects.select_for_update()
        .filter(
            payment=payment,
            provider_refund_id__isnull=True,
            amount_rub=amount,
            status__in=[Refund.Status.CREATED, Refund.Status.PENDING],
        )
        .order_by("created_at")[:2]
    )
    if len(candidates) != 1:
        return False

    refund = candidates[0]
    if refund.wallet_debited_at is None:
        # Defensive legacy recovery only. New refund flows hold before provider POST.
        debit_paid(payment.user, amount, "refund_recover", refund.id)
        refund.wallet_debited_at = timezone.now()
    refund.provider_refund_id = refund_id
    refund.status = Refund.Status.SUCCEEDED
    refund.provider_payload = remote
    refund.save(
        update_fields=[
            "provider_refund_id",
            "status",
            "provider_payload",
            "wallet_debited_at",
            "updated_at",
        ]
    )
    request = RefundRequest.objects.select_for_update().filter(refund=refund).first()
    if request is not None:
        request.status = RefundRequest.Status.SUCCEEDED
        request.resolved_at = timezone.now()
        request.save(update_fields=["status", "resolved_at", "updated_at"])
    return True


@transaction.atomic
def register_unknown_succeeded_refund(payload, *, client):
    """Materialize or recover provider-side refunds unknown by provider id locally."""
    if not isinstance(payload, dict) or payload.get("event") != "refund.succeeded":
        return False
    object_data = payload.get("object") or {}
    refund_id = str(object_data.get("id") or "").strip()
    if not refund_id or Refund.objects.filter(provider_refund_id=refund_id).exists():
        return False

    remote = client.get_refund(refund_id)
    if not isinstance(remote, dict) or remote.get("id") != refund_id:
        raise ValidationError("Provider refund id mismatch")
    if remote.get("status") != Refund.Status.SUCCEEDED:
        raise ValidationError("Provider refund is not succeeded")
    payment_id = str(remote.get("payment_id") or "").strip()
    payment = (
        Payment.objects.select_for_update()
        .select_related("user")
        .filter(provider_payment_id=payment_id, status=Payment.Status.SUCCEEDED)
        .first()
    )
    if payment is None:
        return False
    amount_data = remote.get("amount") or {}
    amount = _money(amount_data.get("value"))
    if amount <= 0 or amount_data.get("currency") != "RUB":
        raise ValidationError("Provider refund amount mismatch")

    if _recover_timed_out_local_refund(
        payment=payment,
        refund_id=refund_id,
        amount=amount,
        remote=remote,
    ):
        return True

    unresolved_same_amount = Refund.objects.filter(
        payment=payment,
        provider_refund_id__isnull=True,
        amount_rub=amount,
        status__in=[Refund.Status.CREATED, Refund.Status.PENDING],
    ).count()
    if unresolved_same_amount > 1:
        # Multiple indistinguishable timed-out POSTs: never guess which wallet hold
        # corresponds to this provider refund. Freeze spend and require manual review.
        _freeze_user_spend(payment.user)
        _notify_financial_incident(
            payment=payment,
            refund_id=refund_id,
            amount=amount,
            detail=(
                f"ЮKassa подтвердила возврат {amount:.2f} ₽ по платежу {payment.id}, "
                "но найдено несколько незавершённых локальных возвратов той же суммы. "
                "Дополнительное списание не выполнено; нужна ручная сверка."
            ),
        )
        return True

    successful_total = (
        payment.refunds.filter(status=Refund.Status.SUCCEEDED).aggregate(total=Sum("amount_rub"))[
            "total"
        ]
        or Decimal("0")
    )
    if successful_total + amount > payment.amount_rub:
        _freeze_user_spend(payment.user)
        _notify_financial_incident(
            payment=payment,
            refund_id=refund_id,
            amount=amount,
            detail=(
                f"Сумма подтверждённых возвратов YooKassa превышает исходный платёж {payment.id}. "
                "Новые расходы пользователя заблокированы до ручной сверки."
            ),
        )
        return True

    key = "external:" + hashlib.sha256(refund_id.encode()).hexdigest()[:55]
    refunded_at = timezone.now()
    wallet_debited = False
    try:
        debit_paid(payment.user, amount, "external_refund", refund_id)
        wallet_debited = True
    except ValidationError:
        _freeze_user_spend(payment.user)
        _notify_financial_incident(
            payment=payment,
            refund_id=refund_id,
            amount=amount,
        )

    Refund.objects.create(
        payment=payment,
        provider_refund_id=refund_id,
        idempotency_key=key,
        amount_rub=amount,
        status=Refund.Status.SUCCEEDED,
        provider_payload=remote,
        wallet_debited_at=refunded_at if wallet_debited else None,
    )
    return True
