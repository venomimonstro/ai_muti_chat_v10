from django.core import signing
from django.urls import reverse
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_registry.models import Provider, ProviderApiKey
from apps.chat.models import Generation, GenerationAttempt

from .issue_models import SystemIssue
from .permissions import IsPlatformAdmin

DIAGNOSTICS_SHARE_SALT = "admin-ops-diagnostics-share-v1"
DIAGNOSTICS_SHARE_MAX_AGE_SECONDS = 6 * 60 * 60


def build_system_diagnostics():
    providers = []
    for provider in Provider.objects.all().order_by("priority", "name"):
        counts = {state: 0 for state, _label in ProviderApiKey.HealthState.choices}
        key_errors = {}
        for state, error_code in provider.api_keys.values_list("health_state", "last_error_code"):
            counts[state] = counts.get(state, 0) + 1
            if error_code:
                key_errors[error_code] = key_errors.get(error_code, 0) + 1
        providers.append({
            "provider": provider.slug,
            "enabled": provider.enabled,
            "emergency_disabled": provider.emergency_disabled,
            "health": provider.health_state,
            "consecutive_failures": provider.consecutive_failures,
            "circuit_opened_until": provider.circuit_opened_until,
            "last_checked_at": provider.last_checked_at,
            "last_latency_ms": provider.last_latency_ms,
            "credential_source": provider.credential_source(),
            "keys": counts,
            "key_error_codes": key_errors,
        })

    generations = list(
        Generation.objects.filter(state__in=[Generation.State.FAILED, Generation.State.CANCELLED])
        .order_by("-created_at")[:100]
    )
    recent_errors = []
    for generation in generations:
        recent_errors.append({
            "generation_id": str(generation.id),
            "correlation_id": str(generation.correlation_id),
            "state": generation.state,
            "internal_error_code": generation.error_code,
            "provider": generation.provider_slug,
            "routed_model": generation.routed_model,
            "actual_cost_rub": generation.actual_cost_rub,
            "created_at": generation.created_at,
            "completed_at": generation.completed_at,
            "attempts": list(
                GenerationAttempt.objects.filter(generation=generation)
                .order_by("sequence")
                .values(
                    "sequence", "provider__slug", "model_slug", "state",
                    "error_code", "retryable", "latency_ms",
                    "started_at", "finished_at",
                )
            ),
        })

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

    open_issues = sum(1 for item in issues if item["status"] in {"open", "investigating"})
    unhealthy_providers = sum(
        1 for item in providers
        if item["health"] not in {"healthy", "unknown"} or item["emergency_disabled"]
    )

    return {
        "schema_version": 2,
        "generated_at": timezone.now(),
        "summary": {
            "open_system_issues": open_issues,
            "recent_failed_generations": len(recent_errors),
            "unhealthy_providers": unhealthy_providers,
            "providers_total": len(providers),
        },
        "providers": providers,
        "recent_chat_errors": recent_errors,
        "system_issues": issues,
        "privacy": {
            "api_keys": False,
            "message_text": False,
            "user_identity": False,
            "tracebacks": False,
            "credentials": False,
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
        return Response({
            "url": request.build_absolute_uri(path),
            "expires_in_seconds": DIAGNOSTICS_SHARE_MAX_AGE_SECONDS,
        })


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
            return Response({"detail": "Диагностическая ссылка недействительна или истекла"}, status=404)
        if payload.get("scope") != "system-diagnostics":
            return Response({"detail": "Диагностическая ссылка недействительна"}, status=404)
        return Response(build_system_diagnostics())
