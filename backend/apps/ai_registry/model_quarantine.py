from __future__ import annotations

import sys
import time

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
}
MODEL_SCOPE = "model"


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


def model_runtime_available(model: AIModel) -> bool:
    """A quarantined upstream model is hidden without disabling its provider."""
    try:
        return not _open_incidents(model).exists()
    except Exception:
        # Runtime-readiness is fail-closed: a broken incident query must not expose
        # a model whose state cannot be verified.
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
    now = timezone.now()
    return _open_incidents(model).update(
        state=ReliabilityIncident.State.RECOVERED,
        recovered_at=now,
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


def recover_quarantined_models(*, limit: int = 8) -> dict:
    """Probe quarantined model ids outside customer traffic and recover on success."""
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
            adapter = dispatch.adapter_for(model)
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


def install(*, dispatch_module, adapters_module, reliability_module, router_module) -> None:
    """Install model-level failure isolation without changing public APIs."""
    raw_adapter_for = dispatch_module.adapter_for
    if not getattr(raw_adapter_for, "_ai_workspace_model_identity", False):
        def adapter_for(model, *args, **kwargs):
            adapter = raw_adapter_for(model, *args, **kwargs)
            try:
                adapter._ai_workspace_model_slug = str(model.slug)
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
    if not getattr(raw_ready, "_ai_workspace_model_quarantine", False):
        def model_client_ready(model):
            return model_runtime_available(model) and raw_ready(model)

        model_client_ready._ai_workspace_model_quarantine = True
        model_client_ready._raw_model_client_ready = raw_ready
        reliability_module.model_client_ready = model_client_ready

    raw_failure = reliability_module.record_failure
    if not getattr(raw_failure, "_ai_workspace_model_quarantine", False):
        def record_failure(provider, error, adapter=None):
            model_slug = str(getattr(adapter, "_ai_workspace_model_slug", "") or "").strip()
            if model_slug and is_model_scoped_error(error):
                model = AIModel.objects.filter(slug=model_slug, provider=provider).first()
                if model is not None:
                    quarantine_model(model, error)
                    return None
            return raw_failure(provider, error, adapter=adapter)

        record_failure._ai_workspace_model_quarantine = True
        record_failure._raw_record_failure = raw_failure
        reliability_module.record_failure = record_failure

    raw_success = reliability_module.record_success
    if not getattr(raw_success, "_ai_workspace_model_quarantine", False):
        def record_success(provider, latency_ms, adapter=None):
            result = raw_success(provider, latency_ms, adapter=adapter)
            model_slug = str(getattr(adapter, "_ai_workspace_model_slug", "") or "").strip()
            if model_slug:
                model = AIModel.objects.filter(slug=model_slug, provider=provider).first()
                if model is not None:
                    recover_model(model)
            return result

        record_success._ai_workspace_model_quarantine = True
        record_success._raw_record_success = raw_success
        reliability_module.record_success = record_success

    raw_route_row = router_module._route_row
    if not getattr(raw_route_row, "_ai_workspace_model_quarantine", False):
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

    # Modules imported before AppConfig.ready() keep function objects by value.
    bindings = {
        "apps.chat.streaming": {
            "adapter_for": dispatch_module.adapter_for,
            "record_failure": reliability_module.record_failure,
            "record_success": reliability_module.record_success,
        },
        "apps.ai_registry.views": {
            "model_client_ready": reliability_module.model_client_ready,
        },
        "apps.ai_registry.serializers": {
            "model_client_ready": reliability_module.model_client_ready,
        },
        "apps.chat.serializers": {
            "model_client_ready": reliability_module.model_client_ready,
        },
    }
    for module_name, values in bindings.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for name, value in values.items():
            setattr(module, name, value)
