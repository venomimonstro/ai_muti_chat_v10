import hashlib
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.admin_ops.permissions import IsPlatformAdmin
from apps.billing.models import Wallet
from apps.billing.services import credit

from .services import audit


class AdminPromoCreditView(APIView):
    """Credit non-refundable promo RUB directly into a user's wallet.

    The immutable wallet ledger is the financial source of truth. This operation
    is independent from YooKassa, PAYMENTS_ENABLED, payment limits and the
    AdminBalanceAdjustment model. Admin audit is best-effort and must never roll
    back an already committed wallet credit.
    """

    permission_classes = [IsPlatformAdmin]

    def post(self, request, user_id):
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            return Response({"detail": "Пользователь не найден"}, status=404)

        try:
            amount = Decimal(str(request.data.get("amount_rub", ""))).quantize(Decimal("0.0001"))
        except (InvalidOperation, TypeError, ValueError):
            return Response({"detail": "Некорректная сумма"}, status=400)
        if amount <= 0:
            return Response({"detail": "Сумма должна быть больше 0 ₽"}, status=400)

        comment = str(request.data.get("comment", "")).strip()
        if len(comment) < 3:
            return Response({"detail": "Укажите комментарий к начислению"}, status=400)

        raw_key = str(request.headers.get("Idempotency-Key", "")).strip()
        if not raw_key:
            return Response({"detail": "Idempotency-Key обязателен"}, status=400)
        if len(raw_key) > 120:
            return Response({"detail": "Idempotency-Key слишком длинный"}, status=400)

        source_id = hashlib.sha256(
            f"{request.user.id}:{user.id}:{raw_key}".encode("utf-8")
        ).hexdigest()
        try:
            entry = credit(user, amount, "admin_promo", source_id, bucket="promo")
        except ValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=400)

        wallet = Wallet.objects.get(user=user)
        audit_logged = True
        try:
            audit(
                request,
                "user.promo_credit",
                "user",
                user.id,
                {
                    "ledger_entry_id": str(entry.id),
                    "amount_rub": str(amount),
                    "comment": comment,
                    "bucket": "promo",
                    "idempotency_key_hash": hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
                },
            )
        except Exception:
            audit_logged = False

        return Response(
            {
                "ok": True,
                "user_id": str(user.id),
                "amount_rub": str(amount),
                "ledger_entry_id": str(entry.id),
                "audit_logged": audit_logged,
                "wallet": {
                    "available_rub": str(wallet.available_rub),
                    "reserved_rub": str(wallet.reserved_rub),
                    "paid_rub": str(wallet.paid_rub),
                    "promo_rub": str(wallet.promo_rub),
                },
            }
        )
