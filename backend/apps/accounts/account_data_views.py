from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Conversation
from apps.files.models import FileAsset
from apps.payments.models import Payment
from apps.projects.models import Project

from .models import User, UserPreference


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
                "conversations": list(
                    Conversation.objects.filter(owner=user)
                    .order_by("created_at")
                    .values("id", "title", "routing_mode", "created_at", "updated_at")
                ),
                "projects": list(
                    Project.objects.filter(owner=user)
                    .order_by("created_at")
                    .values("id", "name", "description", "created_at", "updated_at", "archived_at")
                ),
                "files": list(
                    FileAsset.objects.filter(owner=user)
                    .order_by("created_at")
                    .values("id", "original_name", "detected_type", "size_bytes", "status", "created_at", "deleted_at")
                ),
                "payments": list(
                    Payment.objects.filter(user=user)
                    .order_by("created_at")
                    .values("id", "amount_rub", "currency", "status", "created_at", "updated_at")
                ),
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
