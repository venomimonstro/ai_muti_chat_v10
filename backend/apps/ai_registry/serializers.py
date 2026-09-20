from rest_framework import serializers

from apps.billing.pricing import active_price

from .models import AIModel
from .reliability import provider_available


class AIModelSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    provider = serializers.CharField(source="provider.slug")
    provider_name = serializers.CharField(source="provider.name")
    available = serializers.SerializerMethodField()
    health_state = serializers.CharField(source="provider.health_state")
    price = serializers.SerializerMethodField()
    model_version = serializers.SerializerMethodField()
    exact_api_id = serializers.CharField(source="upstream_model")

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

    def get_display_name(self, obj):
        name = (obj.display_name or obj.upstream_model or obj.slug).strip()
        provider = (obj.provider.name or obj.provider.slug).strip()
        if name.casefold().startswith(provider.casefold()):
            return name
        return f"{provider} · {name}"

    def get_available(self, obj):
        return obj.enabled and provider_available(obj.provider)

    def get_model_version(self, obj):
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
