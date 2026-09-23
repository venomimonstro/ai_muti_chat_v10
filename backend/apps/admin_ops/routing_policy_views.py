from django.db import transaction
from rest_framework.response import Response

from apps.ai_registry.models import AIModel, Provider, RoutingPolicyVersion
from apps.ai_registry.router import DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS
from apps.billing.pricing import active_price, quote, require_margin

from .services import audit
from .views import AdminAPIView


TIER_TO_MODE = {
    "weak": "economy",
    "medium": "balanced",
    "high": "maximum",
}
TIER_LABELS = {
    "weak": "Слабая",
    "medium": "Средняя",
    "high": "Высокая",
}


def _active_policy():
    policy = RoutingPolicyVersion.objects.filter(active=True).first()
    if policy:
        return policy
    return RoutingPolicyVersion.objects.create(
        version="admin-routing-v1",
        active=True,
        mode_weights=DEFAULT_WEIGHTS,
        thresholds=DEFAULT_THRESHOLDS,
    )


def _readiness(model):
    problems = []
    provider = model.provider
    if provider.emergency_disabled:
        problems.append("Провайдер аварийно отключён")
    if provider.health_state != Provider.HealthState.HEALTHY:
        problems.append("Провайдер не прошёл проверку связи")
    if not provider.credential_configured():
        problems.append("Нет рабочего API-ключа")
    if not model.current_version_id:
        problems.append("Нет активной версии модели")
    if not (model.upstream_model or "").strip():
        problems.append("Не указан upstream model")
    try:
        price = active_price(model.slug)
        require_margin(quote(price, 1_000_000, 0, provider_slug=provider.slug, model_slug=model.slug))
        require_margin(quote(price, 0, 1_000_000, provider_slug=provider.slug, model_slug=model.slug))
    except Exception:
        problems.append("Не настроена безопасная коммерческая цена")
    return list(dict.fromkeys(problems))


def _payload(policy):
    configured = dict((policy.thresholds or {}).get("tier_models") or {})
    models = AIModel.objects.select_related("provider", "current_version").exclude(upstream_model="").order_by(
        "provider__priority", "provider__name", "display_name"
    )
    options = []
    for model in models:
        problems = _readiness(model)
        options.append({
            "slug": model.slug,
            "display_name": model.display_name,
            "upstream_model": model.upstream_model,
            "provider": model.provider.slug,
            "provider_name": model.provider.name,
            "provider_enabled": model.provider.enabled and not model.provider.emergency_disabled,
            "provider_health": model.provider.health_state,
            "model_enabled": model.enabled,
            "ready": not problems,
            "blockers": problems,
        })
    tiers = []
    for tier, mode in TIER_TO_MODE.items():
        slug = configured.get(mode) or ""
        model = next((item for item in options if item["slug"] == slug), None)
        tiers.append({
            "tier": tier,
            "label": TIER_LABELS[tier],
            "mode": mode,
            "model": slug or None,
            "provider": model["provider"] if model else None,
            "upstream_model": model["upstream_model"] if model else None,
        })
    return {
        "policy_version": policy.version,
        "tiers": tiers,
        "models": options,
        "fallback": "Если закреплённый внешний API недоступен, AUTO Router выбирает следующую готовую внешнюю модель. Локальные модели не используются.",
    }


class RoutingTierMatrixView(AdminAPIView):
    def get(self, request):
        return Response(_payload(_active_policy()))

    @transaction.atomic
    def patch(self, request):
        policy = RoutingPolicyVersion.objects.select_for_update().filter(active=True).first() or _active_policy()
        incoming = request.data.get("tiers")
        if not isinstance(incoming, dict):
            return Response({"detail": "Передайте объект tiers: weak, medium, high"}, status=400)
        thresholds = dict(policy.thresholds or {})
        tier_models = dict(thresholds.get("tier_models") or {})
        changed = {}
        for tier, model_slug in incoming.items():
            if tier not in TIER_TO_MODE:
                continue
            mode = TIER_TO_MODE[tier]
            slug = str(model_slug or "").strip()
            if not slug:
                tier_models.pop(mode, None)
                changed[tier] = None
                continue
            model = AIModel.objects.select_related("provider", "current_version").filter(slug=slug).first()
            if model is None:
                return Response({"detail": f"Модель {slug} не найдена"}, status=400)
            problems = _readiness(model)
            if problems:
                return Response({
                    "detail": f"Модель {model.display_name} пока нельзя назначить в AUTO",
                    "model": model.slug,
                    "blockers": problems,
                }, status=409)
            provider = model.provider
            update_fields = []
            if not provider.enabled:
                provider.enabled = True
                update_fields.append("enabled")
            if update_fields:
                provider.save(update_fields=update_fields)
            if not model.enabled:
                model.enabled = True
                model.save(update_fields=["enabled"])
            tier_models[mode] = model.slug
            changed[tier] = model.slug
        thresholds["tier_models"] = tier_models
        policy.thresholds = thresholds
        policy.save(update_fields=["thresholds"])
        audit(request, "routing.tier_matrix.updated", "routing_policy", policy.id, metadata={"tiers": changed})
        return Response(_payload(policy))
