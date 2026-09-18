from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from apps.accounts.models import Notification, SupportRequest

from .services import audit
from .views import AdminAPIView, _limit


class CategorizedSupportControlView(AdminAPIView):
    def get(self, request):
        queryset = SupportRequest.objects.select_related("user", "replied_by").order_by("-created_at")
        if request.query_params.get("status"):
            queryset = queryset.filter(status=request.query_params["status"])
        if request.query_params.get("category"):
            queryset = queryset.filter(category=request.query_params["category"])
        return Response(
            [
                {
                    "id": item.id,
                    "user_id": item.user_id,
                    "user_email": item.user.email,
                    "subject": item.subject,
                    "category": item.category,
                    "category_label": item.get_category_display(),
                    "message": item.message,
                    "status": item.status,
                    "admin_reply": item.admin_reply,
                    "replied_at": item.replied_at,
                    "replied_by": item.replied_by.email if item.replied_by else None,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in queryset[: _limit(request)]
            ]
        )


class SupportStatusReplyView(AdminAPIView):
    @transaction.atomic
    def post(self, request, support_id):
        item = get_object_or_404(
            SupportRequest.objects.select_for_update().select_related("user"),
            pk=support_id,
        )
        new_status = request.data.get("status", item.status)
        if new_status not in SupportRequest.Status.values:
            return Response({"detail": "Недопустимый статус обращения"}, status=400)
        reply = str(request.data.get("reply", "")).strip()
        if len(reply) > 10_000:
            return Response({"detail": "Ответ поддержки слишком длинный"}, status=400)
        fields = ["status", "updated_at"]
        item.status = new_status
        reply_changed = bool(reply and reply != item.admin_reply)
        if reply_changed:
            item.admin_reply = reply
            item.replied_by = request.user
            item.replied_at = timezone.now()
            fields.extend(["admin_reply", "replied_by", "replied_at"])
        item.save(update_fields=fields)
        if reply_changed:
            Notification.objects.create(
                user=item.user,
                title="Поддержка ответила на обращение",
                body=f"По обращению «{item.subject}» появился ответ.",
                level=Notification.Level.INFO,
                action_url="/app/help",
            )
        audit(
            request,
            "support.replied" if reply_changed else "support.status_changed",
            "support_request",
            item.id,
            {"status": item.status, "has_reply": reply_changed},
        )
        return Response(
            {
                "id": item.id,
                "status": item.status,
                "admin_reply": item.admin_reply,
                "replied_at": item.replied_at,
            }
        )
