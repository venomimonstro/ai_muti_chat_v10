from rest_framework.response import Response

from apps.accounts.models import SupportRequest

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
