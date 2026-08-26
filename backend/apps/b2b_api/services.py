import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.models import AIModel
from apps.billing.pricing import (
    active_price,
    calculate_from_snapshot,
    conservative_token_budget,
    quote,
    require_margin,
)
from apps.billing.services import release, reserve, settle

from .keys import key_is_active
from .models import APIKey, APIUsage

ZERO = Decimal("0")


class PublicAPIError(Exception):
    def __init__(self, message, *, code="invalid_request", status_code=400, param=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.param = param


@dataclass(frozen=True)
class CompletionResult:
    usage: APIUsage
    cached: bool


def require_scope(key, scope):
    if scope not in key.scopes:
        raise PublicAPIError(
            f"API key lacks the {scope} scope", code="insufficient_scope", status_code=403
        )


def _month_start(now=None):
    now = now or timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _spent(queryset):
    values = queryset.aggregate(charged=Sum("charged_rub"), pending=Sum("estimated_cost_rub"))
    return (values["charged"] or ZERO), (values["pending"] or ZERO)


def usage_summary(organization, *, api_key=None):
    queryset = APIUsage.objects.filter(organization=organization, created_at__gte=_month_start())
    if api_key is not None:
        queryset = queryset.filter(api_key=api_key)
    completed = queryset.filter(state=APIUsage.State.COMPLETED)
    totals = completed.aggregate(
        spend_rub=Sum("charged_rub"),
        requests=Count("id"),
        prompt_tokens=Sum("prompt_tokens"),
        completion_tokens=Sum("completion_tokens"),
        average_latency_ms=Avg("latency_ms"),
    )
    errors = queryset.filter(state=APIUsage.State.FAILED).count()
    models = list(
        completed.values("model__slug")
        .annotate(requests=Count("id"), spend_rub=Sum("charged_rub"))
        .order_by("model__slug")
    )
    return {
        "period_start": _month_start().isoformat(),
        "spend_rub": str(totals["spend_rub"] or ZERO),
        "requests": totals["requests"] or 0,
        "prompt_tokens": totals["prompt_tokens"] or 0,
        "completion_tokens": totals["completion_tokens"] or 0,
        "errors": errors,
        "average_latency_ms": round(totals["average_latency_ms"] or 0, 2),
        "models": [
            {
                "model": item["model__slug"],
                "requests": item["requests"],
                "spend_rub": str(item["spend_rub"] or ZERO),
            }
            for item in models
        ],
    }


def validate_messages(messages):
    if not isinstance(messages, list) or not messages:
        raise PublicAPIError("messages must be a non-empty array", param="messages")
    normalized = []
    total_chars = 0
    for index, item in enumerate(messages):
        if not isinstance(item, dict) or item.get("role") not in {
            "system",
            "developer",
            "user",
            "assistant",
        }:
            raise PublicAPIError("Unsupported message role", param=f"messages.{index}.role")
        content = item.get("content")
        if not isinstance(content, str):
            raise PublicAPIError(
                "Only text message content is supported",
                code="unsupported_content_type",
                param=f"messages.{index}.content",
            )
        total_chars += len(content)
        normalized.append(
            {"role": "system" if item["role"] == "developer" else item["role"], "content": content}
        )
    if total_chars > settings.B2B_API_MAX_MESSAGE_CHARS:
        raise PublicAPIError("Message payload is too large", code="context_length_exceeded")
    return normalized


def _request_hash(model_slug, messages, max_tokens):
    payload = json.dumps(
        {"model": model_slug, "messages": messages, "max_tokens": max_tokens},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _model_for(key, model_slug):
    if key.allowed_models and model_slug not in key.allowed_models:
        raise PublicAPIError("Model is not allowed for this API key", code="model_not_allowed")
    model = (
        AIModel.objects.select_related("provider", "current_version")
        .filter(
            slug=model_slug,
            enabled=True,
            provider__enabled=True,
            provider__emergency_disabled=False,
        )
        .first()
    )
    if model is None:
        raise PublicAPIError("The requested model does not exist", code="model_not_found", status_code=404)
    return model


@transaction.atomic
def _begin(key, model, estimate, snapshot, idem_key, request_hash):
    locked = APIKey.objects.select_for_update().select_related("organization").get(pk=key.pk)
    if not key_is_active(locked):
        raise PublicAPIError("API key is inactive", code="invalid_api_key", status_code=401)
    if idem_key:
        existing = APIUsage.objects.filter(api_key=locked, idempotency_key=idem_key).first()
        if existing:
            if existing.request_hash != request_hash:
                raise PublicAPIError(
                    "Idempotency-Key was already used with a different request",
                    code="idempotency_key_conflict",
                    status_code=409,
                )
            if existing.state == APIUsage.State.COMPLETED:
                return existing, True
            raise PublicAPIError(
                "A request with this Idempotency-Key is already in progress or failed",
                code="idempotency_key_in_use",
                status_code=409,
            )
    now = timezone.now()
    recent = APIUsage.objects.filter(api_key=locked, created_at__gte=now - timedelta(minutes=1))
    if recent.count() >= locked.rate_limit_per_minute:
        raise PublicAPIError("Rate limit exceeded", code="rate_limit_exceeded", status_code=429)
    running = APIUsage.objects.filter(
        api_key=locked,
        state=APIUsage.State.RUNNING,
        created_at__gte=now - timedelta(seconds=settings.B2B_API_RUNNING_TIMEOUT_SECONDS),
    ).count()
    if running >= locked.max_concurrency:
        raise PublicAPIError(
            "Concurrent request limit exceeded", code="concurrency_limit_exceeded", status_code=429
        )
    period = _month_start(now)
    key_done, _ = _spent(
        APIUsage.objects.filter(api_key=locked, state=APIUsage.State.COMPLETED, created_at__gte=period)
    )
    _, key_pending = _spent(
        APIUsage.objects.filter(api_key=locked, state=APIUsage.State.RUNNING, created_at__gte=period)
    )
    org_done, _ = _spent(
        APIUsage.objects.filter(organization=locked.organization, state=APIUsage.State.COMPLETED, created_at__gte=period)
    )
    _, org_pending = _spent(
        APIUsage.objects.filter(organization=locked.organization, state=APIUsage.State.RUNNING, created_at__gte=period)
    )
    if locked.monthly_limit_rub is not None and key_done + key_pending + estimate > locked.monthly_limit_rub:
        raise PublicAPIError("API key monthly budget exceeded", code="budget_exceeded", status_code=402)
    org_limit = locked.organization.monthly_limit_rub
    if org_limit is not None and org_done + org_pending + estimate > org_limit:
        raise PublicAPIError("Organization monthly budget exceeded", code="budget_exceeded", status_code=402)
    usage = APIUsage.objects.create(
        organization=locked.organization,
        api_key=locked,
        model=model,
        response_id=f"chatcmpl-{uuid.uuid4().hex}",
        idempotency_key=idem_key,
        request_hash=request_hash,
        estimated_cost_rub=estimate,
        pricing_snapshot=snapshot,
    )
    reservation = reserve(
        locked.organization.billing_user, estimate, f"public-api:{usage.id}"
    )
    usage.reservation = reservation
    usage.save(update_fields=["reservation"])
    locked.last_used_at = now
    locked.save(update_fields=["last_used_at"])
    return usage, False


def _fail(usage, code, started):
    if usage.reservation_id:
        release(usage.reservation_id)
    usage.state = APIUsage.State.FAILED
    usage.error_code = code
    usage.latency_ms = int((time.monotonic() - started) * 1000)
    usage.completed_at = timezone.now()
    usage.save(update_fields=["state", "error_code", "latency_ms", "completed_at"])


def create_completion(*, key, model_slug, messages, max_tokens, idempotency_key="", adapter=None):
    require_scope(key, "chat.completions")
    if key.allowed_endpoints and "chat.completions" not in key.allowed_endpoints:
        raise PublicAPIError("Endpoint is not allowed for this API key", code="endpoint_not_allowed", status_code=403)
    normalized = validate_messages(messages)
    model = _model_for(key, model_slug)
    max_tokens = min(max_tokens, model.max_output_tokens, settings.B2B_API_MAX_OUTPUT_TOKENS)
    input_budget, output_budget = conservative_token_budget(normalized, max_tokens)
    price = active_price(model.slug)
    value = require_margin(
        quote(
            price,
            input_budget,
            output_budget,
            provider_slug=model.provider.slug,
            model_slug=model.slug,
            operation_type="public_api",
            organization_id=key.organization_id,
        )
    )
    request_hash = _request_hash(model.slug, normalized, max_tokens)
    try:
        usage, cached = _begin(
            key,
            model,
            value.user_charge_rub,
            value.pricing_snapshot,
            idempotency_key,
            request_hash,
        )
    except ValidationError as exc:
        raise PublicAPIError(exc.messages[0], code="billing_error", status_code=402) from exc
    except IntegrityError as exc:
        raise PublicAPIError("Idempotency conflict", code="idempotency_key_conflict", status_code=409) from exc
    if cached:
        return CompletionResult(usage, True)
    started = time.monotonic()
    try:
        result = (adapter or adapter_for(model)).generate(
            model=(model.current_version.exact_api_id if model.current_version else model.upstream_model),
            messages=normalized,
            max_output_tokens=max_tokens,
        )
        provider_cost, charge, _profit, _margin = calculate_from_snapshot(
            price, result.input_tokens, result.output_tokens, usage.pricing_snapshot
        )
        if charge > usage.estimated_cost_rub:
            raise PublicAPIError(
                "Provider usage exceeded the reserved maximum",
                code="cost_limit_exceeded",
                status_code=502,
            )
        settle(usage.reservation_id, charge)
        usage.state = APIUsage.State.COMPLETED
        usage.provider_cost_rub = provider_cost
        usage.charged_rub = charge
        usage.prompt_tokens = result.input_tokens
        usage.completion_tokens = result.output_tokens
        usage.provider_request_id = result.provider_request_id
        usage.response_text = result.text
        usage.latency_ms = int((time.monotonic() - started) * 1000)
        usage.completed_at = timezone.now()
        usage.save(update_fields=[
            "state", "provider_cost_rub", "charged_rub", "prompt_tokens",
            "completion_tokens", "provider_request_id", "response_text", "latency_ms",
            "completed_at",
        ])
    except PublicAPIError as exc:
        _fail(usage, exc.code, started)
        raise
    except ProviderError as exc:
        _fail(usage, exc.code, started)
        raise PublicAPIError("Upstream model request failed", code=exc.code, status_code=502) from exc
    except Exception as exc:
        _fail(usage, "internal_error", started)
        raise
    return CompletionResult(usage, False)
