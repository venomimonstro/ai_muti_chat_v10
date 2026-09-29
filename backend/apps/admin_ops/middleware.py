import re
import uuid

from django.utils.deprecation import MiddlewareMixin

from .system_health import record_exception, record_http_5xx

_CORRELATION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


def _correlation_id(request):
    supplied = str(request.headers.get("X-Correlation-ID", "") or "").strip()
    if supplied and _CORRELATION_RE.fullmatch(supplied):
        return supplied
    return str(uuid.uuid4())


class SystemIssueCaptureMiddleware(MiddlewareMixin):
    """Persist server-side HTTP failures for the admin diagnostics center.

    Request bodies, cookies, authorization headers and query values are never
    recorded. Only route/method, exception metadata and a correlation id are sent
    to SystemIssue. This keeps the diagnostic share useful without turning it into
    a request-data archive.
    """

    def process_request(self, request):
        if not getattr(request, "correlation_id", ""):
            request.correlation_id = _correlation_id(request)

    def process_exception(self, request, exception):
        try:
            record_exception(request, exception)
            request._system_issue_recorded = True
        except Exception:
            # Diagnostics must never turn an application exception into a second
            # failure or interfere with Django's normal exception handling.
            pass
        return None

    def process_response(self, request, response):
        correlation_id = str(getattr(request, "correlation_id", "") or "")
        if correlation_id:
            response["X-Correlation-ID"] = correlation_id
        if response.status_code >= 500 and not getattr(request, "_system_issue_recorded", False):
            try:
                record_http_5xx(request, response.status_code)
            except Exception:
                pass
        return response
