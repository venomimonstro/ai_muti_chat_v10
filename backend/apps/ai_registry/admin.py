from django.contrib import admin

from .models import (
    AIModel,
    ModelVersion,
    ModelVersionTransition,
    Provider,
    ProviderHealthSnapshot,
    ReliabilityIncident,
    RoutingPolicyVersion,
    RoutingTierAssignment,
)


def _sync_routing_tiers():
    """Persist the admin-managed tier pool into the active immutable policy payload."""
    policy = RoutingPolicyVersion.objects.filter(active=True).first()
    if policy is None:
        return
    tier_models = {}
    for tier, _label in RoutingTierAssignment.Tier.choices:
        tier_models[tier] = list(
            RoutingTierAssignment.objects.filter(tier=tier, enabled=True)
            .select_related("model")
            .order_by("priority", "model__display_name")
            .values_list("model__slug", flat=True)
        )
    thresholds = dict(policy.thresholds or {})
    thresholds["tier_models"] = tier_models
    RoutingPolicyVersion.objects.filter(pk=policy.pk).update(thresholds=thresholds)


@admin.register(RoutingPolicyVersion)
class RoutingPolicyVersionAdmin(admin.ModelAdmin):
    list_display = ("version", "active", "created_at")
    list_filter = ("active",)

    def has_change_permission(self, request, obj=None):
        return request.method in {"GET", "HEAD", "OPTIONS"}

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RoutingTierAssignment)
class RoutingTierAssignmentAdmin(admin.ModelAdmin):
    list_display = ("tier", "model", "provider", "priority", "enabled", "updated_at")
    list_filter = ("tier", "enabled", "model__provider")
    search_fields = ("model__display_name", "model__slug", "model__provider__name")
    autocomplete_fields = ("model",)
    list_editable = ("priority", "enabled")
    ordering = ("tier", "priority", "model__display_name")

    @admin.display(description="Провайдер")
    def provider(self, obj):
        return obj.model.provider.name

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        _sync_routing_tiers()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        _sync_routing_tiers()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        _sync_routing_tiers()


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display = ("display_name", "provider", "slug", "upstream_model", "enabled")
    list_filter = ("enabled", "provider")
    search_fields = ("display_name", "slug", "upstream_model")


admin.site.register(Provider)
admin.site.register(ModelVersion)
admin.site.register(ModelVersionTransition)
admin.site.register(ProviderHealthSnapshot)
admin.site.register(ReliabilityIncident)
