import hmac
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from .adapters import ProviderError, adapter_for
from .models import (
    AIModel,
    Provider,
    ProviderApiKey,
    ProviderHealthSnapshot,
    ReliabilityIncident,
)


PROVIDER_BLOCKING_ERROR_CODES = {
    "authentication_error",
    "invalid_api_key",
    "credential_missing",
    "permission_denied",
    "insufficient_quota",
    "credit_balance_exhausted",
    "billing_hard_limit_reached",
    "gigachat_oauth_authentication_error",
    "gigachat_oauth_quota_exhausted",
    "gigachat_authentication_error",
    "gigachat_quota_exhausted",
    "gigachat_permission_denied",
}


def _has_healthy_key(provider: Provider) -> bool:
    return ProviderApiKey.objects.filter(
        provider_id=provider.id,
        enabled=True,
        health_state=ProviderApiKey.HealthState.HEALTHY,
    ).exists()


def _has_pool_keys(provider: Provider) -> bool:
    try:
        return ProviderApiKey.objects.filter(
            provider_id=provider.id,
            enabled=True,
        ).exclude(health_state=ProviderApiKey.HealthState.DISABLED).exists()
    except Exception:
        return False


def _normalized_secret(value):
    return str(value or "").removeprefix("Basic ").strip()


def _adapter_secret(adapter):
    return _normalized_secret(
        getattr(adapter, "api_key", "") or getattr(adapter, "authorization_key", "")
    )


def _provider_blocking_error(error: ProviderError) -> bool:
    code = str(error.code or "").strip().casefold()
    if code in PROVIDER_BLOCKING_ERROR_CODES:
        return True
    return code.endswith(
        (
            "_authentication_error",
            "_quota_exhausted",
            "_permission_denied",
            "_credential_missing",
        )
    )


def _most_recent_api_key(provider: Provider):
    """Best-effort key attribution for legacy callers that do not pass an adapter."""
    try:
        return (
            provider.api_keys.filter(enabled=True)
            .exclude(health_state=ProviderApiKey.HealthState.DISABLED)
            .order_by(
                models.F("last_used_at").desc(nulls_last=True),
                "priority",
                "created_at",
            )
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
    """Degrade only the failing key and return whether another key can be probed."""
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
    """Fail-closed customer-traffic readiness predicate.

    Customer requests never act as provider health probes. OPEN, UNKNOWN and
    explicitly disabled external providers stay out of routing until a successful
    health probe records recovery. For key-pool providers at least one verified
    HEALTHY key is required.

    The ECHO adapter is an isolated deterministic test fixture and needs no external
    credential; it follows only the provider's explicit enabled/health controls.
    """
    if not provider.enabled or provider.emergency_disabled:
        return False
    if provider.health_state in {
        Provider.HealthState.UNKNOWN,
        Provider.HealthState.OPEN,
        Provider.HealthState.DISABLED,
    }:
        return False
    if provider.adapter_type == Provider.AdapterType.ECHO:
        return provider.health_state in {
            Provider.HealthState.HEALTHY,
            Provider.HealthState.DEGRADED,
        }
    if not provider.credential_configured():
        return False
    if _has_pool_keys(provider):
        return _has_healthy_key(provider)
    return provider.health_state == Provider.HealthState.HEALTHY


def model_client_ready(model: AIModel) -> bool:
    """Return whether a model is safe to expose or route to a customer now."""
    if not model.enabled or not str(model.upstream_model or "").strip():
        return False
    if model.provider.adapter_type != Provider.AdapterType.ECHO and not model.current_version_id:
        return False
    if not provider_available(model.provider):
        return False
    try:
        from apps.billing.pricing import active_price, quote, require_margin

        price = active_price(model.slug)
        require_margin(
            quote(
                price,
                1_000_000,
                0,
                provider_slug=model.provider.slug,
                model_slug=model.slug,
            )
        )
        require_margin(
            quote(
                price,
                0,
                1_000_000,
                provider_slug=model.provider.slug,
                model_slug=model.slug,
            )
        )
    except Exception:
        return False
    return True


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
        except Exception:
            continue
        model.enabled = True
        model.save(update_fields=["enabled"])
        activated += 1
    return activated


def candidate_models(primary: AIModel) -> list[AIModel]:
    candidates = []
    seen = set()
    current = primary
    while current and current.pk not in seen:
        seen.add(current.pk)
        if model_client_ready(current):
            candidates.append(current)
        current = current.fallback_model
        if current:
            current = AIModel.objects.select_related(
                "provider", "fallback_model", "current_version"
            ).get(pk=current.pk)
    return candidates


@transaction.atomic
def record_failure(provider: Provider, error: ProviderError, adapter=None):
    """Record runtime failure and keep dead credentials away from customers."""
    locked = Provider.objects.select_for_update().get(pk=provider.pk)
    has_spare_key = record_api_key_failure(locked, adapter, error)
    if has_spare_key:
        error.retryable = True
        locked.health_state = Provider.HealthState.DEGRADED
        locked.last_checked_at = timezone.now()
        locked.circuit_opened_until = None
        locked.save(update_fields=["health_state", "last_checked_at", "circuit_opened_until"])
        return

    locked.consecutive_failures += 1
    locked.last_checked_at = timezone.now()

    if _provider_blocking_error(error):
        locked.health_state = Provider.HealthState.OPEN
        locked.circuit_opened_until = None
        if not ReliabilityIncident.objects.filter(
            provider=locked, state=ReliabilityIncident.State.OPEN
        ).exists():
            ReliabilityIncident.objects.create(
                provider=locked,
                error_code=str(error.code or "provider_blocked")[:80],
                details={
                    "consecutive_failures": locked.consecutive_failures,
                    "persistent": True,
                },
            )
        locked.save(
            update_fields=[
                "consecutive_failures",
                "last_checked_at",
                "health_state",
                "circuit_opened_until",
            ]
        )
        return

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
def record_success(provider: Provider, latency_ms: int, adapter=None):
    locked = Provider.objects.select_for_update().get(pk=provider.pk)
    record_api_key_success(locked, adapter, latency_ms)
    was_unhealthy = locked.health_state in {
        Provider.HealthState.OPEN,
        Provider.HealthState.DEGRADED,
        Provider.HealthState.UNKNOWN,
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
    """Probe provider health independently from customer traffic."""
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
        record_failure(provider, exc, adapter=adapter if "adapter" in locals() else None)
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
        record_success(provider, health.latency_ms, adapter=adapter)
    else:
        error = ProviderError("Health check failed", code=health.error_code)
        record_failure(provider, error, adapter=adapter)
    return health
