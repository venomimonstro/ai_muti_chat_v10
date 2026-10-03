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
SPECIAL_EXTERNAL_PROVIDER_SLUGS = {"gigachat", "openrouter", "hubai", "polza"}

# These failures describe one model/request payload, not credential transport health.
# They must never poison a verified API key or hide the whole provider from customer
# traffic. Authentication, permission and quota/balance failures remain blocking.
REQUEST_SCOPED_ERROR_CODES = {
    "bad_request",
    "validation_error",
    "request_too_large",
    "model_not_found",
    "provider_empty_response",
    "gigachat_bad_request",
    "gigachat_validation_error",
    "gigachat_request_too_large",
    "gigachat_model_not_found",
}


def _request_scoped_error(error: ProviderError) -> bool:
    code = str(getattr(error, "code", "") or "").strip().casefold()
    if code in REQUEST_SCOPED_ERROR_CODES:
        return True
    return code.endswith(("_bad_request", "_validation_error", "_request_too_large", "_model_not_found"))


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


def _is_test_echo_provider(provider: Provider) -> bool:
    return (
        provider.adapter_type == Provider.AdapterType.ECHO
        and provider.slug not in SPECIAL_EXTERNAL_PROVIDER_SLUGS
    )


def _normalize_special_external_provider(provider: Provider) -> Provider:
    """Prevent legacy special providers from falling through to EchoAdapter."""
    if (
        provider.slug in SPECIAL_EXTERNAL_PROVIDER_SLUGS
        and provider.adapter_type == Provider.AdapterType.ECHO
    ):
        provider.adapter_type = Provider.AdapterType.OPENAI_RESPONSES
        provider.save(update_fields=["adapter_type"])
    return provider


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
    """Best-effort key attribution only for legacy callers without adapter identity."""
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
    """Resolve only the exact stored key used by a known runtime adapter.

    Guessing a key by ``last_used_at`` is acceptable only for legacy callers that
    provide no adapter at all. When an adapter exists, an unmatched ENV credential
    or a credential deleted/disabled during an in-flight request must never degrade
    an unrelated healthy DB key.
    """
    if adapter is None:
        return _most_recent_api_key(provider)

    key_id = str(getattr(adapter, "_ai_workspace_key_id", "") or "").strip()
    if key_id:
        return provider.api_keys.filter(pk=key_id, enabled=True).first()

    secret = _adapter_secret(adapter)
    if not secret:
        return None
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
    return None


def record_api_key_failure(provider: Provider, adapter, error: ProviderError) -> bool:
    """Degrade only failures that actually prove a credential is unhealthy."""
    if _request_scoped_error(error):
        return False
    key = selected_api_key(provider, adapter)
    if key is None:
        return False
    now = timezone.now()
    ProviderApiKey.objects.filter(pk=key.pk).update(
        health_state=ProviderApiKey.HealthState.DEGRADED,
        last_error_code=str(error.code or "provider_error")[:80],
        last_checked_at=now,
    )
    # Do not retry a customer request on UNKNOWN/DEGRADED credentials. The runtime
    # selector also respects the default procurement account, so this cannot create
    # a request/account mismatch when several paid keys exist for one provider.
    from .dispatch import runtime_credential_ready

    return runtime_credential_ready(provider)


def record_api_key_success(provider: Provider, adapter, latency_ms: int):
    key = selected_api_key(provider, adapter)
    if key is None:
        return
    now = timezone.now()
    ProviderApiKey.objects.filter(pk=key.pk).update(
        health_state=ProviderApiKey.HealthState.HEALTHY,
        last_error_code="",
        last_latency_ms=max(0, int(latency_ms)),
        last_checked_at=now,
        last_used_at=now,
    )


def _procurement_ready(provider: Provider) -> bool:
    """Return whether customer traffic has a HEALTHY funded execution account."""
    if _is_test_echo_provider(provider):
        return True
    try:
        from apps.procurement.account_routing import (
            NATIVE_STEP,
            select_runtime_funding_account,
        )

        account = select_runtime_funding_account(
            provider,
            required_native=NATIVE_STEP,
            allow_probe=False,
            require_balance=True,
        )
        if account is not None:
            return True

        from apps.procurement.models import ProviderFundingAccount

        configured = ProviderFundingAccount.objects.filter(
            provider=provider,
            active=True,
            funded_native__gt=0,
        ).exists()
        # A zero-balance account is only a draft configuration. Merely creating
        # it must never take a verified provider out of customer traffic. The
        # procurement ledger becomes authoritative after real provider credit
        # has been recorded; exhausted purchased credit then remains fail-closed.
        return not configured
    except Exception:
        return False


def provider_available(provider: Provider) -> bool:
    """Fail-closed readiness for real customer traffic.

    UNKNOWN and OPEN providers are never probed by a customer request. Background
    health checks own recovery and re-admit a channel only after a successful probe.
    DEGRADED is allowed only when the runtime can still select a verified HEALTHY
    credential that matches the active procurement account.
    """
    if not provider.enabled or provider.emergency_disabled:
        return False
    if provider.health_state == Provider.HealthState.DISABLED:
        return False
    # Local Echo/test channels do not require an external health probe, so UNKNOWN
    # remains usable in dev/tests. An explicitly OPEN circuit is still authoritative.
    if _is_test_echo_provider(provider):
        return provider.health_state != Provider.HealthState.OPEN
    if provider.health_state not in {
        Provider.HealthState.HEALTHY,
        Provider.HealthState.DEGRADED,
    }:
        return False
    if not provider.credential_configured():
        return False
    from .dispatch import runtime_credential_ready

    if not runtime_credential_ready(provider):
        return False
    return _procurement_ready(provider)


def model_client_ready(model: AIModel) -> bool:
    """Single source of truth for customer-visible/routable model readiness."""
    if not model.enabled or not str(model.upstream_model or "").strip():
        return False
    # ModelVersion is governance/eval metadata. A valid upstream model must not be
    # hidden merely because a local version row has not been promoted yet.
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
    """Return the number of explicitly enabled models that are currently safe.

    This function intentionally does not re-enable administrator-disabled models.
    Readiness and administrative publication are separate controls.
    """
    return sum(
        1
        for model in AIModel.objects.select_related("provider", "current_version").filter(enabled=True)
        if model_client_ready(model)
    )


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
    """Record transport/credential health without poisoning it for request errors."""
    locked = Provider.objects.select_for_update().get(pk=provider.pk)
    if _request_scoped_error(error):
        # A syntactically rejected request proves the provider was reachable and the
        # credential was accepted far enough to validate the payload. Preserve the
        # existing key/provider health; routing may try another compatible model.
        locked.last_checked_at = timezone.now()
        locked.save(update_fields=["last_checked_at"])
        return
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
def record_success(
    provider: Provider,
    latency_ms: int,
    adapter=None,
    *,
    inference_verified: bool = True,
):
    locked = Provider.objects.select_for_update().get(pk=provider.pk)
    # Runtime generation success proves the exact credential can perform paid
    # inference. A metadata-only /models probe must not refresh key last_used_at or
    # erase a blocking quota/billing error.
    if inference_verified:
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
    """Probe provider health independently from customer traffic.

    Metadata endpoints such as /models are not proof that paid inference works.
    A provider recovering from UNKNOWN/DEGRADED/OPEN must therefore complete a tiny
    real inference before it is re-admitted to the customer model catalog. Healthy
    providers keep using the cheaper transport probe during routine revalidation.
    """
    if not provider.enabled or provider.emergency_disabled:
        provider.health_state = Provider.HealthState.DISABLED
        provider.last_checked_at = timezone.now()
        provider.save(update_fields=["health_state", "last_checked_at"])
        return None

    provider = _normalize_special_external_provider(provider)
    recovery_probe_required = provider.health_state in {
        Provider.HealthState.UNKNOWN,
        Provider.HealthState.DEGRADED,
        Provider.HealthState.OPEN,
    }
    force_inference = bool(getattr(provider, "_force_inference_probe", False))
    inference_verified = False
    try:
        model = (
            provider.models.filter(enabled=True)
            .exclude(upstream_model="")
            .order_by("priority", "slug")
            .first()
        )
        if model is None:
            raise ProviderError("Provider has no enabled models", code="no_models", retryable=False)
        from .dispatch import adapter_for as runtime_adapter_for

        adapter = runtime_adapter_for(
            model,
            allow_probe=True,
            require_funding_balance=False,
        )
        health = adapter.health_check()
        key = selected_api_key(provider, adapter)
        key_needs_proof = bool(
            key is not None
            and (
                key.health_state != ProviderApiKey.HealthState.HEALTHY
                or key.last_used_at is None
                or key.last_used_at < timezone.now() - timedelta(minutes=30)
            )
        )
        inference_probe_required = (
            force_inference
            or recovery_probe_required
            or key_needs_proof
        )
        if health.healthy and inference_probe_required and not _is_test_echo_provider(provider):
            started = timezone.now()
            result = adapter.generate(
                model=model.upstream_model,
                messages=[{"role": "user", "content": "Ответь только: OK"}],
                max_output_tokens=8,
            )
            if not str(getattr(result, "text", "") or "").strip():
                raise ProviderError(
                    "Provider recovery inference returned empty output",
                    code="provider_empty_response",
                    retryable=False,
                )
            elapsed = max(
                0,
                int((timezone.now() - started).total_seconds() * 1000),
            )
            health = type(health)(True, elapsed or health.latency_ms)
            inference_verified = True
    except ProviderError as exc:
        record_failure(provider, exc, adapter=adapter if "adapter" in locals() else None)
        ProviderHealthSnapshot.objects.create(
            provider=provider, healthy=False, error_code=exc.code
        )
        return None
    except Exception as exc:
        error = ProviderError(
            "Provider recovery probe failed",
            code="provider_recovery_probe_failed",
            retryable=True,
        )
        record_failure(provider, error, adapter=adapter if "adapter" in locals() else None)
        ProviderHealthSnapshot.objects.create(
            provider=provider,
            healthy=False,
            error_code=error.code,
        )
        return None

    ProviderHealthSnapshot.objects.create(
        provider=provider,
        healthy=health.healthy,
        latency_ms=health.latency_ms,
        error_code=health.error_code,
    )
    if health.healthy:
        record_success(
            provider,
            health.latency_ms,
            adapter=adapter,
            inference_verified=inference_verified,
        )
    else:
        error = ProviderError("Health check failed", code=health.error_code)
        record_failure(provider, error, adapter=adapter)
    return health
