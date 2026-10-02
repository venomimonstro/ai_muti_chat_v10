from __future__ import annotations

import os
import sys
import time

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .adapters import ProviderError
from .models import AIModel, ReliabilityIncident


MODEL_SCOPED_ERROR_CODES = {
    "model_not_found",
    "unsupported_model",
    "invalid_model",
    "unknown_model",
    "gigachat_model_not_found",
    "openrouter_model_not_found",
    "openrouter_404",
}
MODEL_SCOPE = "model"
MODEL_RECOVERY_LOCK_KEY = "ai-registry:model-quarantine-recovery"
MODEL_RECOVERY_LOCK_SECONDS = max(
    300,
    min(int(os.getenv("AI_MODEL_RECOVERY_LOCK_SECONDS", "900")), 3600),
)


class _QuarantinedModelAdapter:
    """Local fail-fast adapter used only for stale in-flight customer routes."""

    def __init__(self, *, model_slug: str, error_code: str):
        self._ai_workspace_model_slug = model_slug
        self._ai_workspace_probe_mode = False
        self.error_code = error_code or "model_not_found"

    def _raise(self):
        raise ProviderError(
            "Model is quarantined after a confirmed upstream model failure",
            code=self.error_code,
            retryable=False,
        )

    def stream(self, **_kwargs):
        self._raise()

    def generate(self, **_kwargs):
        self._raise()

    def health_check(self):
        self._raise()

    def capabilities(self):
        return set()


def is_model_scoped_error(error: ProviderError) -> bool:
    code = str(getattr(error, "code", "") or "").strip().casefold()
    if code in MODEL_SCOPED_ERROR_CODES:
        return True
    return code.endswith(("_model_not_found", "_unsupported_model", "_unknown_model"))


def _open_incidents(model: AIModel):
    return ReliabilityIncident.objects.filter(
        provider_id=model.provider_id,
        state=ReliabilityIncident.State.OPEN,
        details__scope=MODEL_SCOPE,
        details__model_slug=model.slug,
    )


def _has_open_provider_incident(provider) -> bool:
    for incident in ReliabilityIncident.objects.filter(
        provider=provider,
        state=ReliabilityIncident.State.OPEN,
    ).only("details"):
        if (incident.details or {}).get("scope") != MODEL_SCOPE:
            return True
    return False


def _ensure_provider_incident(provider, error: ProviderError):
    """Keep provider outages visible even while one sibling model is quarantined."""
    provider.refresh_from_db(
        fields=["health_state", "consecutive_failures", "circuit_opened_until"]
    )
    if provider.health_state != provider.HealthState.OPEN:
        return
    if _has_open_provider_incident(provider):
        return
    ReliabilityIncident.objects.create(
        provider=provider,
        error_code=str(error.code or "provider_error")[:80],
        details={
            "scope": "provider",
            "consecutive_failures": provider.consecutive_failures,
            "persistent": provider.circuit_opened_until is None,
        },
    )


def model_runtime_available(model: AIModel) -> bool:
    """A quarantined upstream model is hidden without disabling its provider."""
    try:
        return not _open_incidents(model).exists()
    except Exception:
        return False


@transaction.atomic
def quarantine_model(model: AIModel, error: ProviderError):
    """Quarantine exactly one upstream model while preserving sibling capacity."""
    existing = _open_incidents(model).order_by("-opened_at").first()
    details = {
        "scope": MODEL_SCOPE,
        "model_slug": model.slug,
        "upstream_model": str(model.upstream_model or "")[:160],
        "provider_slug": model.provider.slug,
    }
    if existing is not None:
        existing.error_code = str(error.code or "model_unavailable")[:80]
        existing.details = {**(existing.details or {}), **details}
        existing.save(update_fields=["error_code", "details"])
        return existing
    return ReliabilityIncident.objects.create(
        provider=model.provider,
        error_code=str(error.code or "model_unavailable")[:80],
        details=details,
    )


@transaction.atomic
def recover_model(model: AIModel) -> int:
    return _open_incidents(model).update(
        state=ReliabilityIncident.State.RECOVERED,
        recovered_at=timezone.now(),
    )


def quarantine_status(model: AIModel) -> dict:
    incident = _open_incidents(model).order_by("-opened_at").first()
    if incident is None:
        return {"quarantined": False, "error_code": "", "opened_at": None}
    return {
        "quarantined": True,
        "error_code": incident.error_code,
        "opened_at": incident.opened_at,
    }


def _recover_quarantined_models(*, limit: int = 8) -> dict:
    incidents = list(
        ReliabilityIncident.objects.filter(
            state=ReliabilityIncident.State.OPEN,
            details__scope=MODEL_SCOPE,
        )
        .select_related("provider")
        .order_by("opened_at")[: max(1, int(limit))]
    )
    if not incidents:
        return {"checked": 0, "recovered": 0, "still_quarantined": 0, "skipped": 0}

    from . import dispatch, reliability

    checked = recovered = still = skipped = 0
    for incident in incidents:
        slug = str((incident.details or {}).get("model_slug") or "").strip()
        model = (
            AIModel.objects.filter(slug=slug, enabled=True)
            .select_related("provider", "current_version")
            .first()
        )
        if model is None or not str(model.upstream_model or "").strip():
            skipped += 1
            continue
        if not reliability.provider_available(model.provider):
            skipped += 1
            continue

        checked += 1
        adapter = None
        started = time.monotonic()
        try:
            # Recovery is the only path allowed to bypass customer quarantine and
            # touch the upstream model again.
            adapter = dispatch.adapter_for(model, allow_probe=True)
            adapter.generate(
                model=model.upstream_model,
                messages=[{"role": "user", "content": "Ответь только: OK"}],
                max_output_tokens=8,
            )
        except ProviderError as exc:
            if is_model_scoped_error(exc):
                quarantine_model(model, exc)
                still += 1
            else:
                reliability.record_failure(model.provider, exc, adapter=adapter)
                skipped += 1
            continue
        except Exception:
            skipped += 1
            continue

        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        reliability.record_success(model.provider, latency_ms, adapter=adapter)
        recover_model(model)
        recovered += 1

    return {
        "checked": checked,
        "recovered": recovered,
        "still_quarantined": still,
        "skipped": skipped,
    }


def recover_quarantined_models(*, limit: int = 8) -> dict:
    """Probe quarantined model ids outside customer traffic and recover on success.

    A distributed cache lease serializes paid recovery probes across Celery workers.
    Beat runs every five minutes and one sweep may itself take several minutes when
    upstreams time out; without this lease the same model could be probed twice and
    race between OPEN/RECOVERED states. The TTL is deliberately finite so a crashed
    worker cannot block self-healing forever.
    """
    if not cache.add(MODEL_RECOVERY_LOCK_KEY, "1", timeout=MODEL_RECOVERY_LOCK_SECONDS):
        return {
            "checked": 0,
            "recovered": 0,
            "still_quarantined": 0,
            "skipped": 0,
            "status": "skipped",
            "reason": "already_running",
        }
    try:
        result = _recover_quarantined_models(limit=limit)
        return {**result, "status": "ok"}
    finally:
        cache.delete(MODEL_RECOVERY_LOCK_KEY)


def install(*, dispatch_module, adapters_module, reliability_module, router_module) -> None:
    """Install model-level failure isolation without changing public APIs."""
    raw_adapter_for = dispatch_module.adapter_for
    if getattr(raw_adapter_for, "_ai_workspace_model_identity", False) is not True:
        def adapter_for(model, *args, **kwargs):
            probe_mode = bool(kwargs.get("allow_probe", False))
            if not probe_mode and not model_runtime_available(model):
                status = quarantine_status(model)
                return _QuarantinedModelAdapter(
                    model_slug=str(model.slug),
                    error_code=str(status.get("error_code") or "model_not_found"),
                )
            adapter = raw_adapter_for(model, *args, **kwargs)
            try:
                adapter._ai_workspace_model_slug = str(model.slug)
                adapter._ai_workspace_probe_mode = probe_mode
            except Exception:
                pass
            return adapter

        adapter_for._ai_workspace_model_identity = True
        adapter_for._raw_adapter_for = raw_adapter_for
        dispatch_module.adapter_for = adapter_for
    else:
        adapter_for = raw_adapter_for

    adapters_module.adapter_for = adapter_for
    reliability_module.adapter_for = adapter_for

    raw_ready = reliability_module.model_client_ready
    if getattr(raw_ready, "_ai_workspace_model_quarantine", False) is not True:
        def model_client_ready(model):
            return model_runtime_available(model) and raw_ready(model)

        model_client_ready._ai_workspace_model_quarantine = True
        model_client_ready._raw_model_client_ready = raw_ready
        reliability_module.model_client_ready = model_client_ready

    raw_failure = reliability_module.record_failure
    if getattr(raw_failure, "_ai_workspace_model_quarantine", False) is not True:
        def record_failure(provider, error, adapter=None):
            model_slug = str(getattr(adapter, "_ai_workspace_model_slug", "") or "").strip()
            probe_mode = bool(getattr(adapter, "_ai_workspace_probe_mode", False))
            if model_slug and not probe_mode and is_model_scoped_error(error):
                model = AIModel.objects.filter(slug=model_slug, provider=provider).first()
                if model is not None:
                    quarantine_model(model, error)
                    return None
            result = raw_failure(provider, error, adapter=adapter)
            _ensure_provider_incident(provider, error)
            return result

        record_failure._ai_workspace_model_quarantine = True
        record_failure._raw_record_failure = raw_failure
        reliability_module.record_failure = record_failure

    raw_success = reliability_module.record_success
    if getattr(raw_success, "_ai_workspace_model_quarantine", False) is not True:
        def record_success(provider, latency_ms, adapter=None, **kwargs):
            with transaction.atomic():
                model_incident_ids = list(
                    ReliabilityIncident.objects.filter(
                        provider=provider,
                        state=ReliabilityIncident.State.OPEN,
                        details__scope=MODEL_SCOPE,
                    ).values_list("id", flat=True)
                )
                result = raw_success(provider, latency_ms, adapter=adapter, **kwargs)
                if model_incident_ids:
                    ReliabilityIncident.objects.filter(id__in=model_incident_ids).update(
                        state=ReliabilityIncident.State.OPEN,
                        recovered_at=None,
                    )
            return result

        record_success._ai_workspace_model_quarantine = True
        record_success._raw_record_success = raw_success
        reliability_module.record_success = record_success

    raw_route_row = router_module._route_row
    if getattr(raw_route_row, "_ai_workspace_model_quarantine", False) is not True:
        def route_row(model, *args, **kwargs):
            row = raw_route_row(model, *args, **kwargs)
            if not model_runtime_available(model):
                reasons = list(row.get("reasons") or [])
                if "model_quarantined" not in reasons:
                    reasons.append("model_quarantined")
                row["reasons"] = reasons
                row["status"] = "rejected"
            return row

        route_row._ai_workspace_model_quarantine = True
        route_row._raw_route_row = raw_route_row
        router_module._route_row = route_row

    bindings = {
        "apps.chat.streaming": {
            "adapter_for": dispatch_module.adapter_for,
            "record_failure": reliability_module.record_failure,
            "record_success": reliability_module.record_success,
        },
        "apps.ai_registry.views": {"model_client_ready": reliability_module.model_client_ready},
        "apps.ai_registry.serializers": {"model_client_ready": reliability_module.model_client_ready},
        "apps.chat.serializers": {"model_client_ready": reliability_module.model_client_ready},
    }
    for module_name, values in bindings.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for name, value in values.items():
            setattr(module, name, value)
