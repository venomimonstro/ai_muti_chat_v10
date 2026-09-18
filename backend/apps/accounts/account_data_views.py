from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.b2b_api.models import APIKey, Organization, OrganizationMembership
from apps.billing.models import LedgerEntry, Wallet
from apps.chat.models import Conversation, Message
from apps.files.models import FileAsset
from apps.image_studio.models import ImageGeneration
from apps.memory_store.models import MemoryItem
from apps.payments.models import Payment, Refund
from apps.projects.models import Project

from .models import Notification, SupportRequest, User, UserPreference


def _revoke_user_sessions(user_id: str):
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        try:
            if session.get_decoded().get("_auth_user_id") == user_id:
                session.delete()
        except Exception:
            continue


class AccountExportView(APIView):
    def get(self, request):
        user = request.user
        preference = UserPreference.objects.filter(user=user).values().first()
        conversations = list(
            Conversation.objects.filter(owner=user)
            .order_by("created_at")
            .values(
                "id",
                "title",
                "routing_mode",
                "selected_model",
                "project_id",
                "memory_enabled",
                "created_at",
                "updated_at",
            )
        )
        messages = list(
            Message.objects.filter(conversation__owner=user)
            .order_by("created_at")
            .values(
                "id",
                "conversation_id",
                "branch_id",
                "role",
                "content",
                "status",
                "created_at",
            )
        )
        memberships = list(
            OrganizationMembership.objects.filter(user=user)
            .select_related("organization")
            .order_by("created_at")
            .values(
                "organization_id",
                "organization__name",
                "organization__slug",
                "role",
                "status",
                "created_at",
            )
        )
        # Export API-key metadata only. Bearer secrets and secret hashes are deliberately excluded.
        api_keys = list(
            APIKey.objects.filter(created_by=user)
            .order_by("created_at")
            .values(
                "id",
                "organization_id",
                "name",
                "prefix",
                "scopes",
                "allowed_models",
                "allowed_endpoints",
                "monthly_limit_rub",
                "rate_limit_per_minute",
                "max_concurrency",
                "ip_allowlist",
                "expires_at",
                "revoked_at",
                "last_used_at",
                "created_at",
            )
        )
        wallet = Wallet.objects.filter(user=user).first()
        ledger = []
        if wallet:
            ledger = list(
                LedgerEntry.objects.filter(wallet=wallet)
                .order_by("created_at")
                .values(
                    "id",
                    "kind",
                    "amount_rub",
                    "available_delta_rub",
                    "reserved_delta_rub",
                    "paid_delta_rub",
                    "promo_delta_rub",
                    "available_after_rub",
                    "reserved_after_rub",
                    "source_type",
                    "created_at",
                )
            )
        return Response(
            {
                "generated_at": timezone.now(),
                "account": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "email_verified_at": user.email_verified_at,
                    "legal_accepted_at": user.legal_accepted_at,
                    "legal_version": user.legal_version,
                    "date_joined": user.date_joined,
                    "status": user.status,
                },
                "preferences": preference,
                "conversations": conversations,
                "messages": messages,
                "projects": list(
                    Project.objects.filter(owner=user)
                    .order_by("created_at")
                    .values(
                        "id",
                        "name",
                        "description",
                        "created_at",
                        "updated_at",
                        "archived_at",
                    )
                ),
                "files": list(
                    FileAsset.objects.filter(owner=user)
                    .order_by("created_at")
                    .values(
                        "id",
                        "project_id",
                        "original_name",
                        "detected_type",
                        "size_bytes",
                        "status",
                        "created_at",
                        "deleted_at",
                    )
                ),
                "memory": list(
                    MemoryItem.objects.filter(owner=user)
                    .order_by("created_at")
                    .values(
                        "id",
                        "project_id",
                        "conversation_id",
                        "scope",
                        "memory_type",
                        "content",
                        "status",
                        "pinned",
                        "enabled",
                        "created_at",
                        "updated_at",
                    )
                ),
                "image_generations": list(
                    ImageGeneration.objects.filter(owner=user)
                    .order_by("created_at")
                    .values(
                        "id",
                        "model_id",
                        "prompt",
                        "size",
                        "quality",
                        "requested_count",
                        "actual_count",
                        "state",
                        "actual_cost_rub",
                        "created_at",
                        "completed_at",
                    )
                ),
                "payments": list(
                    Payment.objects.filter(user=user)
                    .order_by("created_at")
                    .values(
                        "id",
                        "amount_rub",
                        "currency",
                        "status",
                        "receipt_status",
                        "credited_at",
                        "created_at",
                        "updated_at",
                    )
                ),
                "refunds": list(
                    Refund.objects.filter(payment__user=user)
                    .order_by("created_at")
                    .values(
                        "id",
                        "payment_id",
                        "amount_rub",
                        "status",
                        "wallet_debited_at",
                        "created_at",
                        "updated_at",
                    )
                ),
                "wallet": {
                    "available_rub": wallet.available_rub,
                    "reserved_rub": wallet.reserved_rub,
                    "paid_rub": wallet.paid_rub,
                    "promo_rub": wallet.promo_rub,
                }
                if wallet
                else None,
                "ledger": ledger,
                "support_requests": list(
                    SupportRequest.objects.filter(user=user)
                    .order_by("created_at")
                    .values(
                        "id",
                        "subject",
                        "category",
                        "message",
                        "status",
                        "admin_reply",
                        "replied_at",
                        "created_at",
                        "updated_at",
                    )
                ),
                "notifications": list(
                    Notification.objects.filter(user=user)
                    .order_by("created_at")
                    .values(
                        "id",
                        "title",
                        "body",
                        "level",
                        "action_url",
                        "read_at",
                        "created_at",
                    )
                ),
                "organization_memberships": memberships,
                "api_keys": api_keys,
            }
        )


class AccountDeleteView(APIView):
    @transaction.atomic
    def post(self, request):
        password = str(request.data.get("password", ""))
        confirmation = str(request.data.get("confirmation", ""))
        if confirmation != "DELETE":
            return Response({"detail": "Для удаления введите DELETE"}, status=400)
        user = User.objects.select_for_update().get(pk=request.user.pk)
        if not user.check_password(password):
            return Response({"detail": "Неверный пароль"}, status=400)

        now = timezone.now()
        funded_organization_ids = list(
            Organization.objects.select_for_update()
            .filter(billing_user=user, active=True)
            .values_list("id", flat=True)
        )
        if funded_organization_ids:
            Organization.objects.filter(id__in=funded_organization_ids).update(active=False)
            APIKey.objects.filter(
                organization_id__in=funded_organization_ids,
                revoked_at__isnull=True,
            ).update(revoked_at=now)
        OrganizationMembership.objects.filter(
            user=user,
            status=OrganizationMembership.Status.ACTIVE,
        ).update(status=OrganizationMembership.Status.REMOVED)

        suffix = str(user.id).replace("-", "")
        user.username = f"deleted-{suffix}"[:150]
        user.email = f"deleted+{suffix}@example.invalid"
        user.first_name = ""
        user.last_name = ""
        user.email_verified_at = None
        user.status = User.Status.DELETED
        user.set_unusable_password()
        user.save(
            update_fields=[
                "username",
                "email",
                "first_name",
                "last_name",
                "email_verified_at",
                "status",
                "password",
            ]
        )
        transaction.on_commit(lambda: _revoke_user_sessions(str(user.id)))
        return Response({"deleted": True})
