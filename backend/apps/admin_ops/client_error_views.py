from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsActiveUser

from .client_errors import record_client_error


class ClientErrorReportView(APIView):
    permission_classes = [IsActiveUser]

    def post(self, request):
        error_name = str(request.data.get("error_name", "JavaScriptError"))[:120]
        message = str(request.data.get("message", ""))[:500]
        stack = str(request.data.get("stack", ""))[-8000:]
        source_path = str(request.data.get("source_path", ""))[:220]
        correlation_id = str(request.data.get("correlation_id", ""))[:160]
        record_client_error(
            user=request.user,
            source_path=source_path,
            error_name=error_name,
            message=message,
            stack=stack,
            correlation_id=correlation_id,
        )
        return Response({"accepted": True}, status=202)
