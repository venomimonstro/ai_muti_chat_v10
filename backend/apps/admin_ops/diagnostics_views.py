import json
from datetime import timedelta
from pathlib import Path

from django.core import signing
from django.urls import reverse
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.ai_registry.reliability import model_client_ready, provider_available
from apps.billing.models import BalanceReservation, Wallet
from apps.billing.pricing import active_price
from apps.chat.models import Generation, GenerationAttempt

from .issue_models import SystemIssue
from .permissions import IsPlatformAdmin

DIAGNOSTICS_SHARE_SALT = "admin-ops-diagnostics-share-v1"
DIAGNOSTICS_SHARE_MAX_AGE_SECONDS = 6 * 60 * 60
UPDATE_STATUS_PATH = Path("/app/logs/latest-update-status.json")
STALE_GENERATION_MINUTES = 10
STALE_RESERVATION_MINUTES = 15


def _release_status():
    try:
        payload = json.loads(UPDATE_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return {
        "status": str(payload.get("status") or "unknown")[:32],
        "phase": str(payload.get("phase") or "")[:240],
        "exit_code": payload.get("exit_code"),
        "commit": str(payload.get("commit") or "")[:80],
        "occurred_at": str(payload.get("occurred_at") or "")[:80],
        "log_file": str(payload.get("log_file") or "")[:160],
        "failures": [str(item)[:500] for item in (payload.get("failures") or [])[:40]],
    }


def _model_readiness():
    rows = []
    queryset = AIModel.objects.select_related("provider", "current_version").order_by(
        "provider__priority", "provider__slug", "display_name"
    )
    for model in queryset:
        healthy_keys = model.provider.api_keys.filter(
            enabled=True,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        ).count()
        price_configured = False
        price_error = ""
        try:
            active_price(model.slug)
            price_configured = True
        except Exception as exc:
            price_error = str(exc)[:240]

        reasons = []
        warnings = []
        if not model.enabled:
            reasons.append("model_disabled")
        if not model.upstream_model.strip():
            reasons.append("upstream_missing")
        if model.current_version_id is None:
            # ModelVersion is governance/eval metadata, not a customer-runtime gate.
            warnings.append("active_version_metadata_missing")
        if not model.provider.enabled:
            reasons.append("provider_disabled")
        if model.provider.emergency_disabled:
            reasons.append("provider_emergency_disabled")
        if model.provider.enabled and not provider_available(model.provider):
            reasons.append("provider_unavailable")
        if not price_configured:
            reasons.append("price_missing")

        ready = model_client_ready(model)
        if not ready and not reasons:
            reasons.append("client_readiness_failed")
        rows.append(
            {
                "model": model.slug,
                "display_name": model.display_name,
                "provider": model.provider.slug,
                "enabled": model.enabled,
                "upstream_configured": bool(model.upstream_model.strip()),
                "current_version": model.current_version.version if model.current_version else None,
                "provider_health": model.provider.health_state,
                "healthy_keys": healthy_keys,
                "price_configured": price_configured,
                "price_error": price_error,
                "ready": ready,
                "reasons": list(dict.fromkeys(reasons)),
                "warnings": warnings,
            }
        )
    return rows


def _chat_readiness_summary():
    now = timezone.now()
    since = now - timedelta(hours=24)
    stale_generation_before = now - timedelta(minutes=STALE_GENERATION_MINUTES)
    stale_reservation_before = now - timedelta(minutes=STALE_RESERVATION_MINUTES)
    generations = Generation.objects.filter(created_at__gte=since)
    completed = generations.filter(state=Generation.State.COMPLETED).count()
    failed = generations.filter(state=Generation.State.FAILED).count()
    cancelled = generations.filter(state=Generation.State.CANCELLED).count()
    terminal = completed + failed
    wallets = Wallet.objects.all()
    active_reservations = BalanceReservation.objects.filter(
        state=BalanceReservation.State.ACTIVE
    )
    stale_reservations = active_reservations.filter(
        created_at__lt=stale_reservation_before
    ).count()
    stuck_generations = Generation.objects.filter(
        state__in=[Generation.State.QUEUED, Generation.State.RUNNING],
        created_at__lt=stale_generation_before,
    ).count()
    return {
        "window_hours": 24,
        "completed_generations": completed,
        "failed_generations": failed,
        "cancelled_generations": cancelled,
        "preflight_failed": generations.filter(error_code="preflight_failed").count(),
        "success_rate_percent": round(completed / terminal * 100, 2) if terminal else 100.0,
        "stuck_generations": stuck_generations,
        "active_customer_reservations": active_reservations.count(),
        "stale_customer_reservations": stale_reservations,
        "wallets_total": wallets.count(),
        "wallets_with_positive_balance": wallets.filter(available_rub__gt=0).count(),
        "wallets_without_available_balance": wallets.filter(available_rub__lte=0).count(),
    }


def build_system_diagnostics():
    providers = []
    for provider in Provider.objects.all().order_by("priority", "name"):
        counts = {state: 0 for state, _label in ProviderApiKey.HealthState.choices}
        key_errors = {}
        for state, error_code in provider.api_keys.values_list("health_state", "last_error_code"):
            counts[state] = counts.get(state, 0) + 1
            if error_code:
                key_errors[error_code] = key_errors.get(error_code, 0) + 1
        providers.append(
            {
                "provider": provider.slug,
                "enabled": provider.enabled,
                "emergency_disabled": provider.emergency_disabled,
                "health": provider.health_state,
                "customer_ready": provider_available(provider),
                "consecutive_failures": provider.consecutive_failures,
                "circuit_opened_until": provider.circuit_opened_until,
                "last_checked_at": provider.last_checked_at,
                "last_latency_ms": provider.last_latency_ms,
                "credential_source": provider.credential_source(),
                "keys": counts,
                "key_error_codes": key_errors,
            }
        )

    generations = list(
        Generation.objects.filter(state__in=[Generation.State.FAILED, Generation.State.CANCELLED])
        .order_by("-created_at")[:100]
    )
    recent_errors = []
    for generation in generations:
        recent_errors.append(
            {
                "generation_id": str(generation.id),
                "correlation_id": str(generation.correlation_id),
                "state": generation.state,
                "internal_error_code": generation.error_code,
                "provider": generation.provider_slug,
                "routed_model": generation.routed_model,
                "requested_model": generation.model,
                "actual_cost_rub": generation.actual_cost_rub,
                "created_at": generation.created_at,
                "completed_at": generation.completed_at,
                "attempts": list(
                    GenerationAttempt.objects.filter(generation=generation)
                    .order_by("sequence")
                    .values(
                        "sequence",
                        "provider__slug",
                        "model_slug",
                        "state",
                        "error_code",
                        "retryable",
                        "latency_ms",
                        "started_at",
                        "finished_at",
                    )
                ),
            }
        )

    issues = list(
        SystemIssue.objects.order_by("-last_seen_at")[:200].values(
            "fingerprint",
            "status",
            "severity",
            "exception_type",
            "summary",
            "source",
            "method",
            "task_id",
            "correlation_id",
            "first_seen_at",
            "last_seen_at",
            "occurrences",
            "resolution_note",
        )
    )

    models = _model_readiness()
    chat_readiness = _chat_readiness_summary()
    open_issues = sum(1 for item in issues if item["status"] in {"open", "investigating"})
    unhealthy_providers = sum(
        1
        for item in providers
        if item["enabled"] and not item["customer_ready"]
    )
    blocked_models = sum(1 for item in models if not item["ready"])
    release_status = _release_status()

    return {
        "schema_version": 5,
        "generated_at": timezone.now(),
        "summary": {
            "open_system_issues": open_issues,
            "recent_failed_generations": len(recent_errors),
            "unhealthy_providers": unhealthy_providers,
            "providers_total": len(providers),
            "blocked_models": blocked_models,
            "routable_models": len(models) - blocked_models,
            "models_total": len(models),
            "release_gate_failed": bool(
                release_status and release_status.get("status") == "failed"
            ),
            "stuck_generations": chat_readiness["stuck_generations"],
            "stale_customer_reservations": chat_readiness["stale_customer_reservations"],
        },
        "release_status": release_status,
        "chat_readiness": chat_readiness,
        "providers": providers,
        "models": models,
        "recent_chat_errors": recent_errors,
        "system_issues": issues,
        "privacy": {
            "api_keys": False,
            "message_text": False,
            "user_identity": False,
            "tracebacks": False,
            "credentials": False,
            "raw_release_logs": False,
        },
    }


class ChatDiagnosticsView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response(build_system_diagnostics())


class ChatDiagnosticsShareCreateView(APIView):
    permission_classes = [IsPlatformAdmin]

    def post(self, request):
        token = signing.dumps(
            {"scope": "system-diagnostics", "v": 1},
            salt=DIAGNOSTICS_SHARE_SALT,
            compress=True,
        )
        path = reverse("admin-diagnostics-share", kwargs={"token": token})
        return Response(
            {
                "url": request.build_absolute_uri(path),
                "expires_in_seconds": DIAGNOSTICS_SHARE_MAX_AGE_SECONDS,
            }
        )


class SharedDiagnosticsView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        try:
            payload = signing.loads(
                token,
                salt=DIAGNOSTICS_SHARE_SALT,
                max_age=DIAGNOSTICS_SHARE_MAX_AGE_SECONDS,
            )
        except signing.BadSignature:
            return Response(
                {"detail": "Диагностическая ссылка недействительна или истекла"},
                status=404,
            )
        if payload.get("scope") != "system-diagnostics":
            return Response({"detail": "Диагностическая ссылка недействительна"}, status=404)
        return Response(build_system_diagnostics())
