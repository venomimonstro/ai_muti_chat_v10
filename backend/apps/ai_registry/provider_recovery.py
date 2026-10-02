from __future__ import annotations

import os
import time

from django.core.cache import cache
from django.utils import timezone

from .adapters import AdapterHealth, ProviderError
from .models import AIModel, Provider, ProviderHealthSnapshot

MAX_RECOVERY_MODELS = 4
RECOVERY_PROMPT = "Ответь только: OK"
PROVIDER_RECOVERY_LOCK_SECONDS = max(
    600,
    min(int(os.getenv("AI_PROVIDER_RECOVERY_LOCK_SECONDS", "1200")), 3600),
)


def _probe_lock_key(provider: Provider) -> str:
    return f"ai-registry:provider-recovery:{provider.pk}"


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

    A per-provider distributed lease prevents overlapping Celery sweeps from issuing
    duplicate paid probes if a slow upstream outlives the outer watcher lease.
    """
    raw_check = reliability_module.check_provider
    if getattr(raw_check, "_ai_workspace_inference_recovery", False) is True:
        return

    def _check_provider_unlocked(provider: Provider):
        if not provider.enabled or provider.emergency_disabled:
            provider.health_state = Provider.HealthState.DISABLED
            provider.last_checked_at = timezone.now()
            provider.save(update_fields=["health_state", "last_checked_at"])
            return None

        provider = reliability_module._normalize_special_external_provider(provider)
        force_inference = bool(getattr(provider, "_force_inference_probe", False))
        recovering = (
            provider.health_state != Provider.HealthState.HEALTHY
            or force_inference
        )
        configured_models = list(
            AIModel.objects.filter(provider=provider)
            .exclude(upstream_model="")
            .select_related("provider", "current_version")
            .order_by("slug")
        )
        all_models = [model for model in configured_models if model.enabled]

        # Provider transport health and client model publication are separate
        # concerns. An administrator may intentionally keep every model disabled
        # while validating a new API key. Do not turn a working provider red merely
        # because no model is currently published to customers.
        if not configured_models:
            ProviderHealthSnapshot.objects.create(
                provider=provider,
                healthy=provider.health_state == Provider.HealthState.HEALTHY,
                error_code="no_configured_models",
            )
            return None

        from . import dispatch, model_quarantine

        # The health endpoint verifies the credential/provider itself. Probe mode
        # intentionally bypasses model quarantine for this metadata-level check.
        health_adapter = None
        try:
            health_adapter = dispatch.adapter_for(
                configured_models[0],
                allow_probe=True,
                require_funding_balance=False,
            )
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

        # Funding readiness is a commercial/customer gate, not provider transport
        # health. If an explicitly configured purchasing ledger is empty, keep the
        # provider API green and let provider_available()/model_client_ready() block
        # customer traffic with the precise procurement reason. Do not spend an
        # untracked paid inference probe merely to restore a transport status.
        if recovering and not reliability_module._procurement_ready(provider):
            reliability_module.record_success(
                provider, health.latency_ms, adapter=health_adapter, inference_verified=False
            )
            ProviderHealthSnapshot.objects.create(
                provider=provider,
                healthy=True,
                latency_ms=health.latency_ms,
                error_code="procurement_not_ready",
            )
            return AdapterHealth(
                healthy=True,
                latency_ms=health.latency_ms,
                error_code="procurement_not_ready",
            )

        if not recovering or not all_models:
            reliability_module.record_success(
                provider, health.latency_ms, adapter=health_adapter, inference_verified=False
            )
            ProviderHealthSnapshot.objects.create(
                provider=provider,
                healthy=True,
                latency_ms=health.latency_ms,
                error_code="" if all_models else "no_enabled_models",
            )
            return AdapterHealth(
                healthy=True,
                latency_ms=health.latency_ms,
                error_code="" if all_models else "no_enabled_models",
            )

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
            reliability_module.record_success(provider, health.latency_ms, adapter=health_adapter, inference_verified=False)
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
            reliability_module.record_success(
                provider,
                latency_ms,
                adapter=adapter,
                inference_verified=True,
            )
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

    def check_provider(provider: Provider):
        key = _probe_lock_key(provider)
        if not cache.add(key, "1", timeout=PROVIDER_RECOVERY_LOCK_SECONDS):
            # Another worker is already proving this exact channel. Do not mutate
            # health from the follower and do not let customer traffic act as probe.
            return AdapterHealth(
                healthy=provider.health_state == Provider.HealthState.HEALTHY,
                latency_ms=provider.last_latency_ms or 0,
                error_code="probe_in_progress",
            )
        try:
            return _check_provider_unlocked(provider)
        finally:
            cache.delete(key)

    check_provider._ai_workspace_inference_recovery = True
    check_provider._raw_check_provider = raw_check
    check_provider._unlocked = _check_provider_unlocked
    reliability_module.check_provider = check_provider
