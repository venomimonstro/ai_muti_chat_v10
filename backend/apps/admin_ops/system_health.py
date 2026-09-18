import hashlib
import json
import logging
import os
import traceback
from datetime import timedelta
from pathlib import Path

from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone

from apps.ai_registry.models import Provider
from apps.chat.models import Generation
from apps.payments.models import Payment

logger = logging.getLogger("aiworkspace.system")

INDEX_KEY = "system_issues:index:v1"
ISSUE_PREFIX = "system_issues:item:v1:"
MAX_ISSUES = 500
TTL_SECONDS = 60 * 60 * 24 * 30
LOG_FILE = Path(os.getenv("SYSTEM_ISSUE_LOG_FILE", "/app/logs/system_issues.jsonl"))


def _fingerprint(*, exception_type: str, source: str, summary: str) -> str:
    raw = f"{exception_type}|{source}|{summary[:180]}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:32]


def _issue_key(fingerprint: str) -> str:
    return f"{ISSUE_PREFIX}{fingerprint}"


def _safe_user_id(request):
    user = getattr(request, "user", None)
    return str(user.id) if user is not None and user.is_authenticated else None


def _append_jsonl(issue):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(issue, ensure_ascii=False, default=str) + "\n")
    except OSError:
        logger.exception("Не удалось записать системную ошибку в persistent-журнал")


def _store_issue(
    *,
    exception_type: str,
    summary: str,
    source: str,
    traceback_text: str,
    correlation_id: str = "",
    user_id: str | None = None,
    method: str = "",
    task_id: str = "",
):
    fingerprint = _fingerprint(
        exception_type=exception_type,
        source=source,
        summary=summary,
    )
    key = _issue_key(fingerprint)
    now = timezone.now().isoformat()
    current = cache.get(key) or {}
    issue = {
        "fingerprint": fingerprint,
        "status": current.get("status", "open"),
        "severity": "critical",
        "exception_type": exception_type,
        "summary": summary[:500],
        "path": source[:240],
        "method": method[:16],
        "task_id": task_id[:160],
        "correlation_id": correlation_id[:160],
        "user_id": user_id,
        "first_seen_at": current.get("first_seen_at", now),
        "last_seen_at": now,
        "occurrences": int(current.get("occurrences", 0)) + 1,
        "resolution_note": current.get("resolution_note", ""),
        "sample_traceback": traceback_text[-8000:],
    }
    cache.set(key, issue, timeout=TTL_SECONDS)
    index = list(cache.get(INDEX_KEY) or [])
    if fingerprint in index:
        index.remove(fingerprint)
    index.insert(0, fingerprint)
    cache.set(INDEX_KEY, index[:MAX_ISSUES], timeout=TTL_SECONDS)
    _append_jsonl(issue)
    logger.error(
        "Системная ошибка fingerprint=%s correlation_id=%s source=%s type=%s summary=%s",
        fingerprint,
        correlation_id,
        source,
        exception_type,
        summary[:300],
    )
    return issue


def record_exception(request, exc: Exception):
    exception_type = type(exc).__name__
    summary = str(exc).strip() or exception_type
    return _store_issue(
        exception_type=exception_type,
        summary=summary,
        source=getattr(request, "path", ""),
        method=getattr(request, "method", ""),
        correlation_id=str(getattr(request, "correlation_id", "")),
        user_id=_safe_user_id(request),
        traceback_text="".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ),
    )


def record_background_exception(*, task_name: str, task_id: str, exc: Exception):
    exception_type = type(exc).__name__
    summary = str(exc).strip() or exception_type
    return _store_issue(
        exception_type=exception_type,
        summary=summary,
        source=f"celery:{task_name}",
        task_id=task_id,
        traceback_text="".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ),
    )


def record_http_5xx(request, status_code: int):
    class HTTPServerError(Exception):
        pass

    error = HTTPServerError(f"HTTP {status_code} без перехваченного исключения")
    return record_exception(request, error)


def list_issues(*, status: str | None = None, limit: int = 100):
    result = []
    for fingerprint in list(cache.get(INDEX_KEY) or []):
        issue = cache.get(_issue_key(fingerprint))
        if not issue:
            continue
        if status and issue.get("status") != status:
            continue
        result.append(issue)
        if len(result) >= limit:
            break
    return result


def update_issue(fingerprint: str, *, status: str, resolution_note: str = ""):
    if status not in {"open", "investigating", "resolved", "ignored"}:
        raise ValueError("Недопустимый статус")
    key = _issue_key(fingerprint)
    issue = cache.get(key)
    if not issue:
        return None
    issue["status"] = status
    issue["resolution_note"] = resolution_note[:2000]
    issue["updated_at"] = timezone.now().isoformat()
    cache.set(key, issue, timeout=TTL_SECONDS)
    _append_jsonl({**issue, "event": "status_changed"})
    return issue


def system_analysis():
    now = timezone.now()
    hour = now - timedelta(hours=1)
    day = now - timedelta(hours=24)
    generation_day = Generation.objects.filter(created_at__gte=day)
    generation_hour = Generation.objects.filter(created_at__gte=hour)
    day_total = generation_day.count()
    day_failed = generation_day.filter(state=Generation.State.FAILED).count()
    hour_total = generation_hour.count()
    hour_failed = generation_hour.filter(state=Generation.State.FAILED).count()
    open_issues = list_issues(status="open", limit=MAX_ISSUES)
    investigating = list_issues(status="investigating", limit=MAX_ISSUES)
    unhealthy = list(
        Provider.objects.filter(enabled=True).exclude(
            health_state=Provider.HealthState.HEALTHY
        ).values("slug", "name", "health_state", "last_latency_ms", "last_checked_at")
    )
    payment_failures = Payment.objects.filter(
        created_at__gte=day, status=Payment.Status.CANCELED
    ).count()
    top_errors = list(
        generation_day.filter(state=Generation.State.FAILED)
        .values("error_code")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )
    risk_score = 0
    risk_score += min(40, len(open_issues) * 5)
    risk_score += min(25, len(unhealthy) * 5)
    risk_score += min(25, round((day_failed / day_total * 100) if day_total else 0))
    risk_score += min(10, payment_failures)
    risk_score = min(100, risk_score)
    if risk_score >= 60:
        state = "critical"
    elif risk_score >= 25:
        state = "warning"
    else:
        state = "healthy"
    return {
        "state": state,
        "risk_score": risk_score,
        "generated_at": now,
        "issues": {
            "open": len(open_issues),
            "investigating": len(investigating),
            "recent": list_issues(limit=20),
        },
        "ai": {
            "requests_1h": hour_total,
            "failed_1h": hour_failed,
            "error_rate_1h_percent": round(hour_failed / hour_total * 100, 2) if hour_total else 0,
            "requests_24h": day_total,
            "failed_24h": day_failed,
            "error_rate_24h_percent": round(day_failed / day_total * 100, 2) if day_total else 0,
            "top_error_codes": top_errors,
        },
        "providers": {"unhealthy_count": len(unhealthy), "unhealthy": unhealthy},
        "payments": {"failed_or_canceled_24h": payment_failures},
    }
