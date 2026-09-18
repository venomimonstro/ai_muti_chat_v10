import json
import logging
import time
import uuid

from django.core.cache import cache

logger = logging.getLogger("aiworkspace.http")


def _metric(name, value=1):
    key = f"metric:{name}"
    try:
        cache.add(key, 0, timeout=None)
        cache.incr(key, value)
    except Exception:
        return


def _record_exception(request, exc):
    try:
        from apps.admin_ops.system_health import record_exception

        record_exception(request, exc)
    except Exception:
        logger.exception("Не удалось зарегистрировать системную ошибку")


def _record_5xx(request, status_code):
    try:
        from apps.admin_ops.system_health import record_http_5xx

        record_http_5xx(request, status_code)
    except Exception:
        logger.exception("Не удалось зарегистрировать HTTP 5xx")


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        incoming = request.headers.get("X-Correlation-ID", "")
        try:
            correlation_id = str(uuid.UUID(incoming)) if incoming else str(uuid.uuid4())
        except (ValueError, TypeError):
            correlation_id = str(uuid.uuid4())
        request.correlation_id = correlation_id
        try:
            response = self.get_response(request)
        except Exception as exc:
            _metric("http_requests_total")
            _metric("http_5xx_total")
            _record_exception(request, exc)
            raise

        latency_ms = int((time.monotonic() - started) * 1000)
        response.setdefault("X-Correlation-ID", correlation_id)
        response.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "form-action 'self'; img-src 'self' data:; object-src 'none'; "
            "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
        )
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.setdefault("X-Permitted-Cross-Domain-Policies", "none")

        _metric("http_requests_total")
        _metric(f"http_status_{response.status_code}")
        _metric("http_latency_ms_total", latency_ms)
        if response.status_code >= 500:
            _metric("http_5xx_total")
            _record_5xx(request, response.status_code)
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "correlation_id": correlation_id,
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "latency_ms": latency_ms,
                    "user_id": str(request.user.id)
                    if getattr(request, "user", None) and request.user.is_authenticated
                    else None,
                },
                ensure_ascii=False,
            )
        )
        return response
