from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework.response import Response

from apps.ai_registry.models import AIModel, RoutingPolicyVersion
from apps.ai_registry.router import DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS

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


def _payload(policy):
    configured = dict((policy.thresholds or {}).get("tier_models") or {})
    models = AIModel.objects.select_related("provider").filter(enabled=True).order_by(
        "provider__priority", "provider__name", "display_name"
    )
    options = [
        {
            "slug": model.slug,
            "display_name": model.display_name,
            "upstream_model": model.upstream_model,
            "provider": model.provider.slug,
            "provider_name": model.provider.name,
            "provider_enabled": model.provider.enabled and not model.provider.emergency_disabled,
            "provider_health": model.provider.health_state,
        }
        for model in models
    ]
    tiers = []
    for tier, mode in TIER_TO_MODE.items():
        slug = configured.get(mode) or ""
        model = next((item for item in options if item["slug"] == slug), None)
        tiers.append(
            {
                "tier": tier,
                "label": TIER_LABELS[tier],
                "mode": mode,
                "model": slug or None,
                "provider": model["provider"] if model else None,
                "upstream_model": model["upstream_model"] if model else None,
            }
        )
    return {
        "policy_version": policy.version,
        "tiers": tiers,
        "models": options,
        "fallback": "Если закреплённая модель недоступна, AUTO Router выбирает следующий подходящий внешний API по текущим правилам качества, цены и здоровья.",
    }


class RoutingTierMatrixView(AdminAPIView):
    def get(self, request):
        return Response(_payload(_active_policy()))

    @transaction.atomic
    def patch(self, request):
        policy = RoutingPolicyVersion.objects.select_for_update().filter(active=True).first()
        if policy is None:
            policy = _active_policy()
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
            try:
                model = AIModel.objects.select_related("provider").get(slug=slug, enabled=True)
            except AIModel.DoesNotExist:
                return Response({"detail": f"Модель {slug} не найдена или выключена"}, status=400)
            if not model.provider.enabled or model.provider.emergency_disabled:
                return Response({"detail": f"Провайдер модели {slug} выключен"}, status=409)
            tier_models[mode] = model.slug
            changed[tier] = model.slug
        thresholds["tier_models"] = tier_models
        policy.thresholds = thresholds
        policy.save(update_fields=["thresholds"])
        audit(request, "routing.tier_matrix.updated", "routing_policy", policy.id, metadata={"tiers": changed})
        return Response(_payload(policy))
