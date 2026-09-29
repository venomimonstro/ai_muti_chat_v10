import hmac
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


def _normalized_secret(value):
    return str(value or "").removeprefix("Basic ").strip()


def _adapter_secret(adapter):
    return _normalized_secret(
        getattr(adapter, "api_key", "") or getattr(adapter, "authorization_key", "")
    )


def _most_recent_api_key(provider: Provider):
    """Best-effort key attribution for request paths that only pass Provider.

    Provider.select_api_key() updates last_used_at immediately before building an
    adapter, so the newest usable key is the credential that most likely served
    the current attempt. Direct adapter-aware paths still use exact secret matching.
    """
    try:
        return (
            provider.api_keys.filter(enabled=True)
            .exclude(health_state=ProviderApiKey.HealthState.DISABLED)
            .order_by("-last_used_at", "priority", "created_at")
            .first()
        )
    except Exception:
        return None


def selected_api_key(provider: Provider, adapter=None):
    """Resolve the stored key used by an adapter without persisting/logging secrets."""
    if adapter is None:
        return _most_recent_api_key(provider)
    secret = _adapter_secret(adapter)
    if not secret:
        return _most_recent_api_key(provider)
    try:
        keys = provider.api_keys.filter(enabled=True).exclude(
            health_state=ProviderApiKey.HealthState.DISABLED
        )
        for key in keys:
            candidate = _normalized_secret(key.get_secret())
            if candidate and hmac.compare_digest(candidate, secret):
                return key
    except Exception:
        return None
    return _most_recent_api_key(provider)


def record_api_key_failure(provider: Provider, adapter, error: ProviderError) -> bool:
    """Degrade only the failing key and return whether another key can retry."""
    key = selected_api_key(provider, adapter)
    if key is None:
        return False
    now = timezone.now()
    ProviderApiKey.objects.filter(pk=key.pk).update(
        health_state=ProviderApiKey.HealthState.DEGRADED,
        last_error_code=str(error.code or "provider_error")[:80],
        last_checked_at=now,
    )
    return ProviderApiKey.objects.filter(
        provider_id=provider.id,
        enabled=True,
        health_state__in=(
            ProviderApiKey.HealthState.HEALTHY,
            ProviderApiKey.HealthState.UNKNOWN,
        ),
    ).exclude(pk=key.pk).exists()


def record_api_key_success(provider: Provider, adapter, latency_ms: int):
    key = selected_api_key(provider, adapter)
    if key is None:
        return
    ProviderApiKey.objects.filter(pk=key.pk).update(
        health_state=ProviderApiKey.HealthState.HEALTHY,
        last_error_code="",
        last_latency_ms=max(0, int(latency_ms)),
        last_checked_at=timezone.now(),
        last_used_at=timezone.now(),
    )


def provider_available(provider: Provider) -> bool:
    """Return whether a provider may receive a customer request right now.

    A healthy stored API key is configuration evidence, but it must never
    override a runtime OPEN circuit. After cooldown we allow one half-open probe;
    only record_success() closes the circuit.
    """
    if provider.emergency_disabled:
        return False

    now = timezone.now()
    if provider.health_state == Provider.HealthState.OPEN:
        if not provider.circuit_opened_until:
            return False
        if provider.circuit_opened_until > now:
            return False
        return bool(provider.enabled and provider.credential_configured())

    healthy_key = _has_healthy_key(provider)
    if not provider.enabled:
        if not healthy_key:
            return False
        provider.enabled = True
        provider.save(update_fields=["enabled"])

    return provider.health_state != Provider.HealthState.DISABLED or healthy_key


@transaction.atomic
def ensure_safe_client_models() -> int:
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
    """Record a runtime failure without sacrificing a provider that has spare keys.

    A credential-specific 401/402/403/quota/rate-limit is first isolated to the
    selected key. If another healthy/unknown key exists, the same chat attempt is
    made retryable and the provider circuit is kept available so the next adapter
    construction rotates credentials automatically.
    """
    locked = Provider.objects.select_for_update().get(pk=provider.pk)
    has_spare_key = record_api_key_failure(locked, None, error)
    if has_spare_key:
        error.retryable = True
        locked.health_state = Provider.HealthState.DEGRADED
        locked.last_checked_at = timezone.now()
        locked.circuit_opened_until = None
        locked.save(update_fields=["health_state", "last_checked_at", "circuit_opened_until"])
        return

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
    record_api_key_success(locked, None, latency_ms)
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
        adapter = adapter_for(model)
        health = adapter.health_check()
    except ProviderError as exc:
        health = None
        if "adapter" in locals():
            record_api_key_failure(provider, adapter, exc)
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
        record_api_key_success(provider, adapter, health.latency_ms)
        record_success(provider, health.latency_ms)
    else:
        error = ProviderError("Health check failed", code=health.error_code)
        record_api_key_failure(provider, adapter, error)
        record_failure(provider, error)
    return health
