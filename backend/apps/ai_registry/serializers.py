from rest_framework import serializers

from apps.billing.pricing import active_price

from .models import AIModel
from .reliability import provider_available


PUBLIC_PROVIDER_NAMES = {
    "gigachat": "LLM System",
    "openai": "ChatGPT",
    "deepseek": "DeepSeek",
    "anthropic": "Claude",
    "gemini": "Gemini",
    "xai": "Grok",
}


class AIModelSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    provider = serializers.CharField(source="provider.slug")
    provider_name = serializers.SerializerMethodField()
    available = serializers.SerializerMethodField()
    health_state = serializers.CharField(source="provider.health_state")
    price = serializers.SerializerMethodField()
    model_version = serializers.SerializerMethodField()
    exact_api_id = serializers.SerializerMethodField()

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
        )

    def get_provider_name(self, obj):
        return PUBLIC_PROVIDER_NAMES.get(obj.provider.slug, obj.provider.name or obj.provider.slug)

    def get_display_name(self, obj):
        if obj.provider.slug == "gigachat":
            lowered = (obj.slug or obj.upstream_model or "").casefold()
            if "max" in lowered:
                return "System Max"
            if "pro" in lowered:
                return "System Pro"
            return "System Lite"
        name = (obj.display_name or obj.upstream_model or obj.slug).strip()
        provider = self.get_provider_name(obj)
        if name.casefold().startswith(provider.casefold()):
            return name
        return name

    def get_exact_api_id(self, obj):
        # Internal implementation IDs are not part of the customer contract.
        return "" if obj.provider.slug == "gigachat" else obj.upstream_model

    def get_available(self, obj):
        if not obj.enabled or not provider_available(obj.provider):
            return False
        try:
            return active_price(obj.slug) is not None
        except Exception:
            return False

    def get_model_version(self, obj):
        if obj.provider.slug == "gigachat":
            return None
        return obj.current_version.version if obj.current_version else None

    def get_price(self, obj):
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
