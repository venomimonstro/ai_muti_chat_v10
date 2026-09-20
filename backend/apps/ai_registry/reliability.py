from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .adapters import ProviderError, adapter_for
from .models import (
    AIModel,
    Provider,
    ProviderApiKey,
    ProviderHealthSnapshot,
    ReliabilityIncident,
)


def _has_healthy_key(provider: Provider) -> bool:
    return ProviderApiKey.objects.filter(
        provider_id=provider.id,
        enabled=True,
        health_state=ProviderApiKey.HealthState.HEALTHY,
    ).exists()


def provider_available(provider: Provider) -> bool:
    if provider.emergency_disabled:
        return False

    # A verified key is the strongest runtime signal we have. If the provider
    # still carries stale disabled/open/degraded state from an earlier failure,
    # heal that state automatically. Commercial model activation remains the
    # gate for exposing a model to clients, and pricing/margin is checked by the
    # router before any paid request is started.
    if _has_healthy_key(provider):
        dirty = False
        if not provider.enabled:
            provider.enabled = True
            dirty = True
        if provider.health_state in {
            Provider.HealthState.OPEN,
            Provider.HealthState.DEGRADED,
            Provider.HealthState.DISABLED,
            Provider.HealthState.UNKNOWN,
        }:
            provider.health_state = Provider.HealthState.HEALTHY
            provider.consecutive_failures = 0
            provider.circuit_opened_until = None
            provider.last_checked_at = timezone.now()
            dirty = True
        if dirty:
            provider.save(
                update_fields=[
                    "enabled",
                    "health_state",
                    "consecutive_failures",
                    "circuit_opened_until",
                    "last_checked_at",
                ]
            )
        return True

    if not provider.enabled:
        return False
    if provider.health_state != Provider.HealthState.OPEN:
        return True
    return bool(provider.circuit_opened_until and provider.circuit_opened_until <= timezone.now())


@transaction.atomic
def ensure_safe_client_models() -> int:
    """Recover saved models that are commercially safe but still disabled.

    This repairs interrupted admin activation flows. It never enables a model
    without a healthy provider key, an active model version, an active price,
    and margin above the configured floor for both input and output.
    """
    from apps.billing.pricing import active_price, quote, require_margin

    activated = 0
    candidates = (
        AIModel.objects.select_for_update()
        .select_related("provider")
        .filter(enabled=False, current_version__isnull=False)
    )
    for model in candidates:
        provider = model.provider
        if provider.emergency_disabled or not _has_healthy_key(provider):
            continue
        try:
            price = active_price(model.slug)
            require_margin(
                quote(
                    price,
                    1_000_000,
                    0,
                    provider_slug=provider.slug,
                    model_slug=model.slug,
                )
            )
            require_margin(
                quote(
                    price,
                    0,
                    1_000_000,
                    provider_slug=provider.slug,
                    model_slug=model.slug,
                )
            )
        except (ValidationError, Exception):
            continue
        model.enabled = True
        model.save(update_fields=["enabled"])
        provider_available(provider)
        activated += 1
    return activated


def candidate_models(primary: AIModel) -> list[AIModel]:
    candidates = []
    seen = set()
    current = primary
    while current and current.pk not in seen:
        seen.add(current.pk)
        if current.enabled and provider_available(current.provider):
            candidates.append(current)
        current = current.fallback_model
        if current:
            current = AIModel.objects.select_related("provider", "fallback_model").get(pk=current.pk)
    return candidates


@transaction.atomic
def record_failure(provider: Provider, error: ProviderError):
    locked = Provider.objects.select_for_update().get(pk=provider.pk)
    locked.consecutive_failures += 1
    locked.last_checked_at = timezone.now()
    threshold = settings.AI_CIRCUIT_FAILURE_THRESHOLD
    if error.retryable and locked.consecutive_failures >= threshold:
        locked.health_state = Provider.HealthState.OPEN
        locked.circuit_opened_until = timezone.now() + timedelta(
            seconds=settings.AI_CIRCUIT_COOLDOWN_SECONDS
        )
        if not ReliabilityIncident.objects.filter(
            provider=locked, state=ReliabilityIncident.State.OPEN
        ).exists():
            ReliabilityIncident.objects.create(
                provider=locked,
                error_code=error.code,
                details={"consecutive_failures": locked.consecutive_failures},
            )
    else:
        locked.health_state = Provider.HealthState.DEGRADED
    locked.save(
        update_fields=[
            "consecutive_failures",
            "last_checked_at",
            "health_state",
            "circuit_opened_until",
        ]
    )


@transaction.atomic
def record_success(provider: Provider, latency_ms: int):
    locked = Provider.objects.select_for_update().get(pk=provider.pk)
    was_unhealthy = locked.health_state in {
        Provider.HealthState.OPEN,
        Provider.HealthState.DEGRADED,
    }
    locked.health_state = Provider.HealthState.HEALTHY
    locked.consecutive_failures = 0
    locked.circuit_opened_until = None
    locked.last_checked_at = timezone.now()
    locked.last_latency_ms = latency_ms
    locked.save(
        update_fields=[
            "health_state",
            "consecutive_failures",
            "circuit_opened_until",
            "last_checked_at",
            "last_latency_ms",
        ]
    )
    if was_unhealthy:
        ReliabilityIncident.objects.filter(
            provider=locked, state=ReliabilityIncident.State.OPEN
        ).update(state=ReliabilityIncident.State.RECOVERED, recovered_at=timezone.now())


def check_provider(provider: Provider):
    if not provider.enabled or provider.emergency_disabled:
        provider.health_state = Provider.HealthState.DISABLED
        provider.last_checked_at = timezone.now()
        provider.save(update_fields=["health_state", "last_checked_at"])
        return None
    try:
        model = provider.models.filter(enabled=True).first()
        if model is None:
            raise ProviderError("Provider has no enabled models", code="no_models", retryable=False)
        health = adapter_for(model).health_check()
    except ProviderError as exc:
        health = None
        record_failure(provider, exc)
        ProviderHealthSnapshot.objects.create(
            provider=provider, healthy=False, error_code=exc.code
        )
        return None
    ProviderHealthSnapshot.objects.create(
        provider=provider,
        healthy=health.healthy,
        latency_ms=health.latency_ms,
        error_code=health.error_code,
    )
    if health.healthy:
        record_success(provider, health.latency_ms)
    else:
        record_failure(provider, ProviderError("Health check failed", code=health.error_code))
    return health
