from decimal import Decimal, InvalidOperation

from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import SupportRequest, User
from apps.admin_ops.permissions import IsPlatformAdmin
from apps.b2b_api.models import APIKey, OrganizationMembership
from apps.billing.models import AdminBalanceAdjustment, Wallet
from apps.billing.services import admin_adjust_balance
from apps.chat.models import Generation
from apps.payments.models import Payment

from .models import SecurityEvent
from .services import audit


class AdminUserDetailView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request, user_id):
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            return Response({"detail": "User not found"}, status=404)
        wallet = Wallet.objects.filter(user=user).first()
        return Response(
            {
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "email_verified": user.email_verified,
                    "role": user.role,
                    "status": user.status,
                    "date_joined": user.date_joined,
                    "last_login": user.last_login,
                },
                "wallet": (
                    {
                        "available_rub": wallet.available_rub,
                        "reserved_rub": wallet.reserved_rub,
                        "paid_rub": wallet.paid_rub,
                        "promo_rub": wallet.promo_rub,
                    }
                    if wallet
                    else None
                ),
                "balance_adjustments": list(
                    AdminBalanceAdjustment.objects.filter(wallet=wallet)
                    .select_related("admin")
                    .values("id", "direction", "amount_rub", "comment", "admin_id", "created_at")[:50]
                ) if wallet else [],
                "payments": list(
                    Payment.objects.filter(user=user)
                    .order_by("-created_at")
                    .values("id", "amount_rub", "status", "receipt_status", "created_at")[:30]
                ),
                "generations": list(
                    Generation.objects.filter(owner=user)
                    .order_by("-created_at")
                    .values("id", "state", "routed_model", "provider_slug", "actual_cost_rub", "error_code", "created_at")[:30]
                ),
                "support": list(
                    SupportRequest.objects.filter(user=user)
                    .order_by("-created_at")
                    .values("id", "subject", "status", "created_at")[:30]
                ),
                "security_events": list(
                    SecurityEvent.objects.filter(user=user)
                    .order_by("-created_at")
                    .values("id", "category", "severity", "status", "summary", "created_at")[:30]
                ),
                "organizations": list(
                    OrganizationMembership.objects.filter(user=user)
                    .select_related("organization")
                    .values("organization_id", "organization__name", "role")
                ),
            }
        )


class AdminUserActionView(APIView):
    permission_classes = [IsPlatformAdmin]

    @transaction.atomic
    def post(self, request, user_id):
        user = User.objects.select_for_update().filter(pk=user_id).first()
        if user is None:
            return Response({"detail": "User not found"}, status=404)
        action = str(request.data.get("action", ""))
        if user.id == request.user.id and action == "block":
            return Response({"detail": "Cannot block current administrator"}, status=409)

        if action == "block":
            user.status = User.Status.BLOCKED
            user.save(update_fields=["status"])
            APIKey.objects.filter(
                organization__memberships__user=user, revoked_at__isnull=True
            ).update(revoked_at=timezone.now())
            self._revoke_sessions(user)
        elif action == "unblock":
            user.status = User.Status.ACTIVE
            user.save(update_fields=["status"])
        elif action == "logout_all":
            self._revoke_sessions(user)
        elif action in {"balance_credit", "balance_debit"}:
            try:
                amount = Decimal(str(request.data.get("amount_rub", "")))
            except (InvalidOperation, TypeError, ValueError):
                return Response({"detail": "Некорректная сумма"}, status=400)
            direction = (
                AdminBalanceAdjustment.Direction.CREDIT
                if action == "balance_credit"
                else AdminBalanceAdjustment.Direction.DEBIT
            )
            try:
                adjustment = admin_adjust_balance(
                    target_user=user,
                    admin=request.user,
                    direction=direction,
                    amount=amount,
                    comment=request.data.get("comment", ""),
                )
            except ValidationError as exc:
                return Response({"detail": str(exc)}, status=400)
            audit(
                request,
                f"user.{action}",
                "user",
                user.id,
                {"adjustment_id": str(adjustment.id), "amount_rub": str(adjustment.amount_rub), "comment": adjustment.comment},
            )
            return Response(
                {
                    "id": user.id,
                    "action": action,
                    "adjustment_id": adjustment.id,
                    "amount_rub": adjustment.amount_rub,
                    "direction": adjustment.direction,
                }
            )
        else:
            return Response({"detail": "Unsupported action"}, status=400)
        audit(request, f"user.{action}", "user", user.id, {"reason": request.data.get("reason", "")})
        return Response({"id": user.id, "status": user.status, "action": action})

    def _revoke_sessions(self, user):
        user_id = str(user.id)
        for session in Session.objects.filter(expire_date__gte=timezone.now()):
            try:
                if session.get_decoded().get("_auth_user_id") == user_id:
                    session.delete()
            except Exception:
                continue
