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

    Recovery is deliberately batched. If one sweep quarantines every model it probed
    but other unquarantined models remain, the provider stays non-routable until a
    later sweep proves one of those remaining models. A real customer therefore never
    becomes the first inference probe merely because the provider has more models than
    one recovery batch.
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
        all_models = list(
            AIModel.objects.filter(provider=provider, enabled=True)
            .exclude(upstream_model="")
            .select_related("provider", "current_version")
            .order_by("slug")
        )
        if not all_models:
            error = ProviderError("Provider has no enabled models", code="no_models", retryable=False)
            reliability_module.record_failure(provider, error, adapter=None)
            ProviderHealthSnapshot.objects.create(
                provider=provider, healthy=False, error_code=error.code
            )
            return None

        from . import dispatch, model_quarantine

        # The health endpoint verifies the credential/provider itself. Probe mode
        # intentionally bypasses model quarantine for this metadata-level check.
        health_adapter = None
        try:
            health_adapter = dispatch.adapter_for(all_models[0], allow_probe=True)
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

        # Already quarantined models are recovered by the dedicated model watcher.
        # They must not consume this provider-recovery batch repeatedly and starve a
        # healthy sibling that has not yet been proved.
        probe_models = [
            model for model in all_models if model_quarantine.model_runtime_available(model)
        ]
        batch = probe_models[:MAX_RECOVERY_MODELS]

        # Provider credentials can be healthy while every configured model is under
        # model-level quarantine. In that case it is safe to restore provider health:
        # model_client_ready() still hides every quarantined model from customers.
        if not batch:
            reliability_module.record_success(provider, health.latency_ms, adapter=health_adapter)
            ProviderHealthSnapshot.objects.create(
                provider=provider,
                healthy=True,
                latency_ms=health.latency_ms,
                error_code="all_models_quarantined",
            )
            return AdapterHealth(
                healthy=True,
                latency_ms=health.latency_ms,
                error_code="all_models_quarantined",
            )

        # Recovery must prove the paid inference path, not merely /models or another
        # metadata endpoint. Model-scoped failures are isolated and another enabled
        # sibling is tried before declaring the whole provider unusable.
        model_failures = 0
        for model in batch:
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

        # Every model in this batch failed for model-scoped reasons. If an untested,
        # unquarantined sibling still exists, keep the provider out of customer
        # traffic and let the next background sweep prove that sibling. Do not call
        # record_failure(): the provider/key itself just passed its health check.
        remaining = [
            model for model in all_models if model_quarantine.model_runtime_available(model)
        ]
        if remaining:
            provider.last_checked_at = timezone.now()
            provider.save(update_fields=["last_checked_at"])
            ProviderHealthSnapshot.objects.create(
                provider=provider,
                healthy=False,
                latency_ms=health.latency_ms,
                error_code="recovery_batch_pending",
            )
            return AdapterHealth(
                healthy=False,
                latency_ms=health.latency_ms,
                error_code="recovery_batch_pending",
            )

        # Credential/provider endpoint is healthy and all configured model ids have
        # now been quarantined. Provider health remains separate from model health;
        # customer catalog stays empty until the dedicated model recovery proves one.
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
