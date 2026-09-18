from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsPlatformAdmin
from .services import audit
from .system_health import list_issues, system_analysis, update_issue


class SystemAnalysisView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        try:
            limit = min(max(int(request.query_params.get("limit", 100)), 1), 500)
        except (TypeError, ValueError):
            limit = 100
        issue_status = request.query_params.get("status") or None
        return Response(
            {
                "analysis": system_analysis(),
                "issues": list_issues(status=issue_status, limit=limit),
            }
        )


class SystemIssueActionView(APIView):
    permission_classes = [IsPlatformAdmin]

    def post(self, request, fingerprint):
        new_status = str(request.data.get("status", "")).strip()
        note = str(request.data.get("resolution_note", "")).strip()
        try:
            issue = update_issue(fingerprint, status=new_status, resolution_note=note)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if issue is None:
            return Response({"detail": "Ошибка не найдена или срок хранения истёк"}, status=404)
        audit(
            request,
            "system_issue.status_changed",
            "system_issue",
            fingerprint,
            {"status": new_status, "resolution_note": note[:500]},
        )
        return Response(issue)
