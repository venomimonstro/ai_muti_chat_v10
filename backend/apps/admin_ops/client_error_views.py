from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from .client_errors import record_client_error


class ClientErrorThrottle(SimpleRateThrottle):
    scope = "client_error"

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            ident = f"user:{user.pk}"
        else:
            ident = f"ip:{self.get_ident(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ClientErrorReportView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ClientErrorThrottle]

    def post(self, request):
        error_name = str(request.data.get("error_name", "JavaScriptError"))[:120]
        message = str(request.data.get("message", ""))[:500]
        stack = str(request.data.get("stack", ""))[-8000:]
        source_path = str(request.data.get("source_path", ""))[:220]
        correlation_id = str(request.data.get("correlation_id", ""))[:160]
        user = request.user if request.user and request.user.is_authenticated else None
        record_client_error(
            user=user,
            source_path=source_path,
            error_name=error_name,
            message=message,
            stack=stack,
            correlation_id=correlation_id,
        )
        return Response({"accepted": True}, status=202)
