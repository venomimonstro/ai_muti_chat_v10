from rest_framework import serializers

from apps.billing.pricing import active_price

from .models import AIModel
from .reliability import model_client_ready


PUBLIC_PROVIDER_NAMES = {
    "gigachat": "LLM System",
    "openai": "ChatGPT",
    "deepseek": "DeepSeek",
    "anthropic": "Claude",
    "gemini": "Gemini",
    "xai": "Grok",
}


def _system_tier_name(obj):
    """Map the internal GigaChat model to the customer-facing LLM System tier."""
    identity = " ".join(
        str(value or "")
        for value in (
            obj.upstream_model,
            obj.display_name,
            obj.slug,
            getattr(obj.current_version, "exact_api_id", "") if obj.current_version_id else "",
            getattr(obj.current_version, "version", "") if obj.current_version_id else "",
        )
    ).casefold()
    if "max" in identity:
        return "System Max"
    if "pro" in identity:
        return "System Pro"
    return "System Lite"


class AIModelSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    provider = serializers.SerializerMethodField()
    provider_name = serializers.SerializerMethodField()
    available = serializers.SerializerMethodField()
    health_state = serializers.CharField(source="provider.health_state")
    price = serializers.SerializerMethodField()
    model_version = serializers.SerializerMethodField()
    exact_api_id = serializers.SerializerMethodField()
    routing_tiers = serializers.SerializerMethodField()
    routing_tiers_configured = serializers.SerializerMethodField()

    class Meta:
        model = AIModel
        fields = (
            "slug",
            "display_name",
            "provider",
            "provider_name",
            "model_version",
            "exact_api_id",
            "capabilities",
            "context_window",
            "max_output_tokens",
            "available",
            "health_state",
            "price",
            "routing_tiers",
            "routing_tiers_configured",
        )

    def get_provider(self, obj):
        return "llm-system" if obj.provider.slug == "gigachat" else obj.provider.slug

    def get_provider_name(self, obj):
        return PUBLIC_PROVIDER_NAMES.get(obj.provider.slug, obj.provider.name or obj.provider.slug)

    def get_display_name(self, obj):
        provider = self.get_provider_name(obj)
        if obj.provider.slug == "gigachat":
            return f"{provider} · {_system_tier_name(obj)}"
        name = (obj.display_name or obj.upstream_model or obj.slug).strip()
        if name.casefold().startswith(provider.casefold()):
            return name
        return f"{provider} · {name}"

    def get_exact_api_id(self, obj):
        return "" if obj.provider.slug == "gigachat" else obj.upstream_model

    def get_available(self, obj):
        return model_client_ready(obj)

    def get_model_version(self, obj):
        if obj.provider.slug == "gigachat":
            return None
        return obj.current_version.version if obj.current_version else None

    def get_routing_tiers(self, obj):
        assignments = getattr(obj, "client_routing_tiers", None)
        if assignments is None:
            assignments = obj.routing_tiers.filter(enabled=True).only("tier")
        return [str(item.tier) for item in assignments]

    def get_routing_tiers_configured(self, _obj):
        return bool(self.context.get("routing_tiers_configured", False))

    def get_price(self, obj):
        if not model_client_ready(obj):
            return None
        try:
            value = active_price(obj.slug)
        except Exception:
            return None
        return {
            "version": str(value.id),
            "input_rub_per_million": value.input_rub_per_million,
            "output_rub_per_million": value.output_rub_per_million,
            "markup_percent": value.markup_percent,
            "effective_from": value.effective_from,
        }
