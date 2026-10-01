from __future__ import annotations

import time

from django.utils import timezone

from .adapters import AdapterHealth, ProviderError
from .models import AIModel, Provider, ProviderHealthSnapshot

MAX_RECOVERY_MODELS = 4
RECOVERY_PROMPT = "Ответь только: OK"


def install(reliability_module) -> None:
    """Require a real inference proof before re-admitting an unhealthy provider.

    Lightweight provider health endpoints often validate only authentication and can
    stay green after inference credits are exhausted or the generation endpoint is
    broken. Healthy providers keep the cheap periodic health check; UNKNOWN,
    DEGRADED and OPEN providers must additionally complete a tiny inference outside
    customer traffic before they are marked HEALTHY again.
    """
    raw_check = reliability_module.check_provider
    if getattr(raw_check, "_ai_workspace_inference_recovery", False):
        return

    def check_provider(provider: Provider):
        if not provider.enabled or provider.emergency_disabled:
            provider.health_state = Provider.HealthState.DISABLED
            provider.last_checked_at = timezone.now()
            provider.save(update_fields=["health_state", "last_checked_at"])
            return None

        provider = reliability_module._normalize_special_external_provider(provider)
        recovering = provider.health_state != Provider.HealthState.HEALTHY
        models = list(
            AIModel.objects.filter(provider=provider, enabled=True)
            .exclude(upstream_model="")
            .select_related("provider", "current_version")
            .order_by("slug")[:MAX_RECOVERY_MODELS]
        )
        if not models:
            error = ProviderError("Provider has no enabled models", code="no_models", retryable=False)
            reliability_module.record_failure(provider, error, adapter=None)
            ProviderHealthSnapshot.objects.create(
                provider=provider, healthy=False, error_code=error.code
            )
            return None

        from . import dispatch, model_quarantine

        health_adapter = None
        try:
            health_adapter = dispatch.adapter_for(models[0], allow_probe=True)
            health = health_adapter.health_check()
        except ProviderError as exc:
            reliability_module.record_failure(provider, exc, adapter=health_adapter)
            ProviderHealthSnapshot.objects.create(
                provider=provider, healthy=False, error_code=exc.code
            )
            return None
        except Exception:
            error = ProviderError(
                "Provider health probe failed", code="health_probe_failed", retryable=True
            )
            reliability_module.record_failure(provider, error, adapter=health_adapter)
            ProviderHealthSnapshot.objects.create(
                provider=provider, healthy=False, error_code=error.code
            )
            return None

        if not health.healthy:
            error = ProviderError("Health check failed", code=health.error_code or "health_failed")
            reliability_module.record_failure(provider, error, adapter=health_adapter)
            ProviderHealthSnapshot.objects.create(
                provider=provider,
                healthy=False,
                latency_ms=health.latency_ms,
                error_code=error.code,
            )
            return health

        if not recovering:
            reliability_module.record_success(
                provider, health.latency_ms, adapter=health_adapter
            )
            ProviderHealthSnapshot.objects.create(
                provider=provider, healthy=True, latency_ms=health.latency_ms, error_code=""
            )
            return health

        # Recovery must prove the paid inference path, not merely /models or another
        # metadata endpoint. Model-scoped failures are isolated and another enabled
        # sibling is tried before declaring the whole provider unusable.
        model_failures = 0
        for model in models:
            adapter = None
            started = time.monotonic()
            try:
                adapter = dispatch.adapter_for(model, allow_probe=True)
                adapter.generate(
                    model=model.upstream_model,
                    messages=[{"role": "user", "content": RECOVERY_PROMPT}],
                    max_output_tokens=8,
                )
            except ProviderError as exc:
                if model_quarantine.is_model_scoped_error(exc):
                    model_quarantine.quarantine_model(model, exc)
                    model_failures += 1
                    continue
                reliability_module.record_failure(provider, exc, adapter=adapter)
                ProviderHealthSnapshot.objects.create(
                    provider=provider,
                    healthy=False,
                    latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                    error_code=exc.code,
                )
                return AdapterHealth(
                    healthy=False,
                    latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                    error_code=exc.code,
                )
            except Exception:
                error = ProviderError(
                    "Provider inference recovery probe failed",
                    code="inference_probe_failed",
                    retryable=True,
                )
                reliability_module.record_failure(provider, error, adapter=adapter)
                ProviderHealthSnapshot.objects.create(
                    provider=provider,
                    healthy=False,
                    latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                    error_code=error.code,
                )
                return AdapterHealth(
                    healthy=False,
                    latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                    error_code=error.code,
                )

            latency_ms = max(0, int((time.monotonic() - started) * 1000))
            reliability_module.record_success(provider, latency_ms, adapter=adapter)
            ProviderHealthSnapshot.objects.create(
                provider=provider, healthy=True, latency_ms=latency_ms, error_code=""
            )
            return AdapterHealth(healthy=True, latency_ms=latency_ms)

        # The credential/provider endpoint is healthy, but every configured model id
        # is quarantined. Keep provider health separate from model configuration so
        # sibling models can be fixed/recovered independently without opening a false
        # provider outage. The public catalog remains empty because quarantine is
        # fail-closed at model_client_ready().
        reliability_module.record_success(provider, health.latency_ms, adapter=health_adapter)
        ProviderHealthSnapshot.objects.create(
            provider=provider,
            healthy=True,
            latency_ms=health.latency_ms,
            error_code="all_models_quarantined" if model_failures else "",
        )
        return AdapterHealth(
            healthy=True,
            latency_ms=health.latency_ms,
            error_code="all_models_quarantined" if model_failures else "",
        )

    check_provider._ai_workspace_inference_recovery = True
    check_provider._raw_check_provider = raw_check
    reliability_module.check_provider = check_provider
