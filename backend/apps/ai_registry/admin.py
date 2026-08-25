from django.contrib import admin

from .models import (
    AIModel,
    Provider,
    ProviderHealthSnapshot,
    ReliabilityIncident,
    RoutingPolicyVersion,
)


@admin.register(RoutingPolicyVersion)
class RoutingPolicyVersionAdmin(admin.ModelAdmin):
    list_display = ("version", "active", "created_at")
    list_filter = ("active",)

    def has_change_permission(self, request, obj=None):
        return request.method in {"GET", "HEAD", "OPTIONS"}

    def has_delete_permission(self, request, obj=None):
        return False

admin.site.register(Provider)
admin.site.register(AIModel)
admin.site.register(ProviderHealthSnapshot)
admin.site.register(ReliabilityIncident)
