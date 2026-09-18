import hashlib
import json
import logging
import os
import re
import traceback
from datetime import timedelta
from pathlib import Path

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.ai_registry.models import Provider
from apps.chat.models import Generation
from apps.payments.models import Payment

from .issue_models import SystemIssue

logger = logging.getLogger("aiworkspace.system")

INDEX_KEY = "system_issues:index:v1"
ISSUE_PREFIX = "system_issues:item:v1:"
MAX_ISSUES = 500
TTL_SECONDS = 60 * 60 * 24 * 30
LOG_FILE = Path(os.getenv("SYSTEM_ISSUE_LOG_FILE", "/app/logs/system_issues.jsonl"))
LOG_MAX_BYTES = int(os.getenv("SYSTEM_ISSUE_LOG_MAX_BYTES", str(20 * 1024 * 1024)))
VALID_SEVERITIES = {"warning", "critical"}

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(authorization|api[_-]?key|secret|password|passwd|token|cookie)\b\s*[:=]\s*([^\s,;]+)"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^:\s/@]+:)[^@\s/]+@"), r"\1[REDACTED]@"),
)


def _redact(value: str) -> str:
    result = value or ""
    for pattern, replacement in _SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _fingerprint(*, exception_type: str, source: str, summary: str) -> str:
    raw = f"{exception_type}|{source}|{summary[:180]}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:32]


def _issue_key(fingerprint: str) -> str:
    return f"{ISSUE_PREFIX}{fingerprint}"


def _safe_user_id(request):
    user = getattr(request, "user", None)
    return str(user.id) if user is not None and user.is_authenticated else None


def _serialize(row: SystemIssue) -> dict:
    return {
        "fingerprint": row.fingerprint,
        "status": row.status,
        "severity": row.severity,
        "exception_type": row.exception_type,
        "summary": row.summary,
        "path": row.source,
        "method": row.method,
        "task_id": row.task_id,
        "correlation_id": row.correlation_id,
        "user_id": row.user_reference or None,
        "first_seen_at": row.first_seen_at.isoformat(),
        "last_seen_at": row.last_seen_at.isoformat(),
        "occurrences": row.occurrences,
        "resolution_note": row.resolution_note,
        "sample_traceback": row.sample_traceback,
    }


def _append_jsonl(issue):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size >= LOG_MAX_BYTES:
            rotated = LOG_FILE.with_suffix(LOG_FILE.suffix + ".1")
            rotated.unlink(missing_ok=True)
            LOG_FILE.replace(rotated)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(issue, ensure_ascii=False, default=str) + "\n")
    except OSError:
        logger.exception("Не удалось записать системную ошибку в persistent-журнал")


def _cache_issue(issue: dict):
    fingerprint = issue["fingerprint"]
    cache.set(_issue_key(fingerprint), issue, timeout=TTL_SECONDS)
    index = list(cache.get(INDEX_KEY) or [])
    if fingerprint in index:
        index.remove(fingerprint)
    index.insert(0, fingerprint)
    cache.set(INDEX_KEY, index[:MAX_ISSUES], timeout=TTL_SECONDS)


def _fallback_issue(
    *, fingerprint, severity, exception_type, summary, source, traceback_text,
    correlation_id, user_id, method, task_id,
):
    now = timezone.now().isoformat()
    current = cache.get(_issue_key(fingerprint)) or {}
    return {
        "fingerprint": fingerprint,
        "status": "open" if current.get("status") in {None, "resolved", "ignored"} else current["status"],
        "severity": severity,
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
    severity: str = "critical",
):
    severity = severity if severity in VALID_SEVERITIES else "critical"
    summary = _redact(summary)
    traceback_text = _redact(traceback_text)
    fingerprint = _fingerprint(exception_type=exception_type, source=source, summary=summary)
    now = timezone.now()
    issue = None
    try:
        with transaction.atomic():
            row = SystemIssue.objects.select_for_update().filter(pk=fingerprint).first()
            if row is None:
                row = SystemIssue.objects.create(
                    fingerprint=fingerprint,
                    status=SystemIssue.Status.OPEN,
                    severity=severity,
                    exception_type=exception_type[:160],
                    summary=summary[:500],
                    source=source[:240],
                    method=method[:16],
                    task_id=task_id[:160],
                    correlation_id=correlation_id[:160],
                    user_reference=(user_id or "")[:64],
                    first_seen_at=now,
                    last_seen_at=now,
                    occurrences=1,
                    sample_traceback=traceback_text[-8000:],
                )
            else:
                row.status = (
                    SystemIssue.Status.OPEN
                    if row.status in {SystemIssue.Status.RESOLVED, SystemIssue.Status.IGNORED}
                    else row.status
                )
                row.severity = severity
                row.exception_type = exception_type[:160]
                row.summary = summary[:500]
                row.source = source[:240]
                row.method = method[:16]
                row.task_id = task_id[:160]
                row.correlation_id = correlation_id[:160]
                row.user_reference = (user_id or "")[:64]
                row.last_seen_at = now
                row.occurrences += 1
                row.sample_traceback = traceback_text[-8000:]
                row.save(
                    update_fields=[
                        "status", "severity", "exception_type", "summary", "source", "method",
                        "task_id", "correlation_id", "user_reference", "last_seen_at",
                        "occurrences", "sample_traceback", "updated_at",
                    ]
                )
            issue = _serialize(row)
    except Exception:
        issue = _fallback_issue(
            fingerprint=fingerprint,
            severity=severity,
            exception_type=exception_type,
            summary=summary,
            source=source,
            traceback_text=traceback_text,
            correlation_id=correlation_id,
            user_id=user_id,
            method=method,
            task_id=task_id,
        )
    _cache_issue(issue)
    _append_jsonl(issue)
    logger.error(
        "Системная ошибка severity=%s fingerprint=%s correlation_id=%s source=%s type=%s summary=%s",
        severity,
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
        traceback_text="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        severity="critical",
    )


def record_background_exception(*, task_name: str, task_id: str, exc: Exception):
    exception_type = type(exc).__name__
    summary = str(exc).strip() or exception_type
    return _store_issue(
        exception_type=exception_type,
        summary=summary,
        source=f"celery:{task_name}",
        task_id=task_id,
        traceback_text="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        severity="critical",
    )


def record_http_5xx(request, status_code: int):
    class HTTPServerError(Exception):
        pass

    return record_exception(
        request,
        HTTPServerError(f"HTTP {status_code} без перехваченного исключения"),
    )


def _cached_issues(*, status: str | None, limit: int, severity: str | None = None):
    result = []
    for fingerprint in list(cache.get(INDEX_KEY) or []):
        issue = cache.get(_issue_key(fingerprint))
        if not issue or (status and issue.get("status") != status):
            continue
        if severity and issue.get("severity") != severity:
            continue
        result.append(issue)
        if len(result) >= limit:
            break
    return result


def list_issues(*, status: str | None = None, limit: int = 100, severity: str | None = None):
    try:
        queryset = SystemIssue.objects.all()
        if status:
            queryset = queryset.filter(status=status)
        if severity:
            queryset = queryset.filter(severity=severity)
        return [_serialize(row) for row in queryset.order_by("-last_seen_at")[:limit]]
    except Exception:
        return _cached_issues(status=status, limit=limit, severity=severity)


def update_issue(fingerprint: str, *, status: str, resolution_note: str = ""):
    if status not in SystemIssue.Status.values:
        raise ValueError("Недопустимый статус")
    issue = None
    try:
        with transaction.atomic():
            row = SystemIssue.objects.select_for_update().filter(pk=fingerprint).first()
            if row is not None:
                row.status = status
                row.resolution_note = _redact(resolution_note)[:2000]
                row.save(update_fields=["status", "resolution_note", "updated_at"])
                issue = _serialize(row)
    except Exception:
        issue = None
    if issue is None:
        issue = cache.get(_issue_key(fingerprint))
        if issue is None:
            return None
        issue["status"] = status
        issue["resolution_note"] = _redact(resolution_note)[:2000]
        issue["updated_at"] = timezone.now().isoformat()
    _cache_issue(issue)
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
    critical_open = [item for item in open_issues if item.get("severity") == "critical"]
    critical_investigating = [item for item in investigating if item.get("severity") == "critical"]
    warning_open = [item for item in open_issues if item.get("severity") == "warning"]
    unhealthy = list(
        Provider.objects.filter(enabled=True)
        .exclude(health_state=Provider.HealthState.HEALTHY)
        .values("slug", "name", "health_state", "last_latency_ms", "last_checked_at")
    )
    payment_failures = Payment.objects.filter(
        created_at__gte=day,
        status=Payment.Status.CANCELED,
    ).count()
    top_errors = list(
        generation_day.filter(state=Generation.State.FAILED)
        .values("error_code")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )
    risk_score = min(
        100,
        min(40, len(critical_open) * 8 + len(critical_investigating) * 4 + len(warning_open))
        + min(25, len(unhealthy) * 5)
        + min(25, round((day_failed / day_total * 100) if day_total else 0))
        + min(10, payment_failures),
    )
    state = "critical" if risk_score >= 60 else "warning" if risk_score >= 25 else "healthy"
    return {
        "state": state,
        "risk_score": risk_score,
        "generated_at": now,
        "issues": {
            "open": len(open_issues),
            "investigating": len(investigating),
            "critical_open": len(critical_open),
            "critical_investigating": len(critical_investigating),
            "warning_open": len(warning_open),
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
