from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_registry.models import Provider, ProviderApiKey
from apps.chat.models import Generation, GenerationAttempt

from .permissions import IsPlatformAdmin


class ChatDiagnosticsView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        providers = []
        for provider in Provider.objects.all().order_by("priority", "name"):
            counts = {state: 0 for state, _label in ProviderApiKey.HealthState.choices}
            for state in provider.api_keys.values_list("health_state", flat=True):
                counts[state] = counts.get(state, 0) + 1
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
            })

        generations = list(
            Generation.objects.filter(state__in=[Generation.State.FAILED, Generation.State.CANCELLED])
            .order_by("-created_at")[:50]
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

        return Response({
            "generated_at": timezone.now(),
            "providers": providers,
            "recent_chat_errors": recent_errors,
            "privacy": {
                "api_keys": False,
                "message_text": False,
                "user_identity": False,
                "tracebacks": False,
            },
        })
