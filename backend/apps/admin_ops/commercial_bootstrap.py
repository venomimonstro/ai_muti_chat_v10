import os
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.models import AIModel, ModelVersion, Provider, ProviderHealthSnapshot, RoutingPolicyVersion
from apps.billing.models import FxRateSnapshot, MarginPolicyVersion, MarkupRuleVersion, PriceVersion


@dataclass(frozen=True)
class ProviderTemplate:
    slug: str
    name: str
    adapter_type: str
    credential_env: str
    base_url_env: str
    default_base_url: str
    model_env: str
    model_slug: str
    display_name: str
    capabilities: tuple[str, ...]


PROVIDER_TEMPLATES = (
    ProviderTemplate("openai", "OpenAI", Provider.AdapterType.OPENAI_RESPONSES, "OPENAI_API_KEY", "OPENAI_API_BASE_URL", "https://api.openai.com/v1", "OPENAI_DEFAULT_MODEL", "openai-default", "OpenAI default", ("text", "streaming", "vision", "tools")),
    ProviderTemplate("anthropic", "Anthropic", Provider.AdapterType.ANTHROPIC_MESSAGES, "ANTHROPIC_API_KEY", "ANTHROPIC_API_BASE_URL", "https://api.anthropic.com/v1", "ANTHROPIC_DEFAULT_MODEL", "anthropic-default", "Claude default", ("text", "streaming", "vision", "tools")),
    ProviderTemplate("deepseek", "DeepSeek", Provider.AdapterType.DEEPSEEK_CHAT, "DEEPSEEK_API_KEY", "DEEPSEEK_API_BASE_URL", "https://api.deepseek.com", "DEEPSEEK_DEFAULT_MODEL", "deepseek-default", "DeepSeek default", ("text", "streaming")),
    ProviderTemplate("gemini", "Google Gemini", Provider.AdapterType.GEMINI_GENERATE_CONTENT, "GEMINI_API_KEY", "GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta", "GEMINI_DEFAULT_MODEL", "gemini-default", "Gemini default", ("text", "streaming", "vision", "tools")),
    ProviderTemplate("xai", "xAI", Provider.AdapterType.XAI_CHAT, "XAI_API_KEY", "XAI_API_BASE_URL", "https://api.x.ai/v1", "XAI_DEFAULT_MODEL", "xai-default", "Grok default", ("text", "streaming", "vision", "tools")),
)


def _price_env(model_slug: str, side: str) -> str:
    normalized = model_slug.upper().replace("-", "_")
    return f"AI_PRICE_{normalized}_{side}_RUB_PER_MILLION"


def _decimal_env(name: str) -> Decimal | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except Exception as exc:
        raise ValueError(f"Invalid decimal in {name}") from exc


@transaction.atomic
def bootstrap_commercial_catalog() -> dict:
    created = {"providers": 0, "models": 0, "versions": 0, "prices": 0, "policies": 0}
    now = timezone.now()
    for index, template in enumerate(PROVIDER_TEMPLATES, start=1):
        provider, was_created = Provider.objects.get_or_create(
            slug=template.slug,
            defaults={
                "name": template.name,
                "enabled": False,
                "priority": index * 10,
                "adapter_type": template.adapter_type,
                "api_base_url": os.getenv(template.base_url_env, template.default_base_url),
                "credential_env": template.credential_env,
            },
        )
        if was_created:
            created["providers"] += 1
        else:
            changed = False
            for field, value in {"name": template.name, "adapter_type": template.adapter_type, "credential_env": template.credential_env}.items():
                if getattr(provider, field) != value:
                    setattr(provider, field, value)
                    changed = True
            if not provider.api_base_url:
                provider.api_base_url = os.getenv(template.base_url_env, template.default_base_url)
                changed = True
            if changed:
                provider.save()

        upstream_model = os.getenv(template.model_env, "").strip()
        model, model_created = AIModel.objects.get_or_create(
            slug=template.model_slug,
            defaults={
                "provider": provider,
                "display_name": template.display_name,
                "upstream_model": upstream_model,
                "enabled": False,
                "capabilities": list(template.capabilities),
                "routing_tags": ["commercial-bootstrap"],
            },
        )
        if model_created:
            created["models"] += 1
        if upstream_model and not model.current_version:
            version, version_created = ModelVersion.objects.get_or_create(
                model=model,
                version="bootstrap-v1",
                defaults={
                    "exact_api_id": upstream_model,
                    "capabilities": list(template.capabilities),
                    "routing_tags": ["commercial-bootstrap"],
                    "context_window": model.context_window,
                    "max_output_tokens": model.max_output_tokens,
                    "stage": ModelVersion.Stage.ACTIVE,
                    "activated_at": now,
                    "release_notes": "Created by Sprint 27 commercial bootstrap",
                },
            )
            if version_created:
                created["versions"] += 1
            model.upstream_model = upstream_model
            model.current_version = version
            model.save(update_fields=["upstream_model", "current_version"])
        if not PriceVersion.objects.filter(model_slug=model.slug, active=True).exists():
            input_price = _decimal_env(_price_env(model.slug, "INPUT"))
            output_price = _decimal_env(_price_env(model.slug, "OUTPUT"))
            if input_price is not None and output_price is not None:
                PriceVersion.objects.create(
                    model_slug=model.slug,
                    input_rub_per_million=input_price,
                    output_rub_per_million=output_price,
                    provider_currency="RUB",
                    markup_percent=Decimal("100"),
                    active=True,
                    effective_from=now,
                )
                created["prices"] += 1

    if not RoutingPolicyVersion.objects.filter(active=True).exists():
        RoutingPolicyVersion.objects.create(
            version="commercial-bootstrap-v1",
            active=True,
            mode_weights={
                "economy": {"quality": 0.25, "cost": 0.55, "latency": 0.20},
                "balanced": {"quality": 0.50, "cost": 0.30, "latency": 0.20},
                "maximum": {"quality": 0.80, "cost": 0.05, "latency": 0.15},
            },
            thresholds={"minimum_quality": 0, "maximum_fallback_cost_multiplier": 1.5},
        )
        created["policies"] += 1
    if not MarkupRuleVersion.objects.filter(scope_type=MarkupRuleVersion.Scope.GLOBAL, active=True).exists():
        MarkupRuleVersion.objects.create(
            scope_type=MarkupRuleVersion.Scope.GLOBAL,
            scope_key="",
            markup_percent=Decimal("100"),
            price_multiplier=Decimal("1"),
            active=True,
            effective_from=now,
            reason="Sprint 27 commercial bootstrap default",
        )
        created["policies"] += 1
    if not MarginPolicyVersion.objects.filter(active=True).exists():
        MarginPolicyVersion.objects.create(
            minimum_gross_margin_percent=Decimal("25"),
            anomaly_cost_deviation_percent=Decimal("20"),
            reconciliation_threshold_rub=Decimal("1"),
            active=True,
            effective_from=now,
        )
        created["policies"] += 1
    if not FxRateSnapshot.objects.filter(base_currency="RUB", quote_currency="RUB", source="identity").exists():
        FxRateSnapshot.objects.create(
            base_currency="RUB",
            quote_currency="RUB",
            rate=Decimal("1"),
            source="identity",
            source_reference="Sprint 27 commercial bootstrap",
            effective_at=now,
        )
        created["policies"] += 1
    return created


def provider_setup_status(provider: Provider) -> dict:
    configured = bool(provider.credential_env and os.getenv(provider.credential_env, "").strip())
    now = timezone.now()
    models = []
    for model in provider.models.select_related("current_version").all():
        price = (
            PriceVersion.objects.filter(
                model_slug=model.slug,
                active=True,
                effective_from__lte=now,
                input_rub_per_million__gt=0,
                output_rub_per_million__gt=0,
            )
            .order_by("-effective_from", "-created_at")
            .first()
        )
        models.append(
            {
                "slug": model.slug,
                "enabled": model.enabled,
                "upstream_model": model.upstream_model,
                "has_active_version": bool(model.current_version_id),
                "has_active_price": price is not None,
            }
        )
    return {
        "slug": provider.slug,
        "name": provider.name,
        "enabled": provider.enabled,
        "credential_env": provider.credential_env,
        "credential_configured": configured,
        "health_state": provider.health_state,
        "models": models,
    }


def commercial_setup_status() -> dict:
    providers = [provider_setup_status(item) for item in Provider.objects.prefetch_related("models")]
    now = timezone.now()
    return {
        "providers": providers,
        "routing_policy": RoutingPolicyVersion.objects.filter(active=True).exists(),
        "markup_policy": MarkupRuleVersion.objects.filter(
            scope_type=MarkupRuleVersion.Scope.GLOBAL,
            active=True,
            effective_from__lte=now,
        ).exists(),
        "margin_policy": MarginPolicyVersion.objects.filter(active=True, effective_from__lte=now).exists(),
        "rub_fx_identity": FxRateSnapshot.objects.filter(
            base_currency="RUB", quote_currency="RUB", source="identity", effective_at__lte=now
        ).exists(),
    }


def check_provider_health(provider: Provider) -> dict:
    model = provider.models.filter(current_version__isnull=False).select_related("provider").first()
    if model is None:
        model = provider.models.select_related("provider").first()
    if model is None:
        return {"healthy": False, "latency_ms": None, "error_code": "model_missing"}
    try:
        health = adapter_for(model).health_check()
        provider.health_state = Provider.HealthState.HEALTHY if health.healthy else Provider.HealthState.DEGRADED
        provider.last_latency_ms = health.latency_ms
        provider.last_checked_at = timezone.now()
        provider.save(update_fields=["health_state", "last_latency_ms", "last_checked_at"])
        ProviderHealthSnapshot.objects.create(
            provider=provider,
            healthy=health.healthy,
            latency_ms=health.latency_ms,
            error_code=health.error_code,
        )
        return {"healthy": health.healthy, "latency_ms": health.latency_ms, "error_code": health.error_code}
    except ProviderError as exc:
        provider.health_state = Provider.HealthState.DEGRADED
        provider.last_checked_at = timezone.now()
        provider.save(update_fields=["health_state", "last_checked_at"])
        ProviderHealthSnapshot.objects.create(provider=provider, healthy=False, error_code=exc.code)
        return {"healthy": False, "latency_ms": None, "error_code": exc.code}
