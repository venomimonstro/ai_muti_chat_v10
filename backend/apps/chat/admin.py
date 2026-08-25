from django.contrib import admin

from .models import (
    CompareRun,
    CompareVariant,
    Conversation,
    ConversationBranch,
    Generation,
    GenerationAttempt,
    Message,
    RoutingDecision,
)

admin.site.register(Conversation)
admin.site.register(Message)
admin.site.register(Generation)
admin.site.register(GenerationAttempt)
admin.site.register(ConversationBranch)


class CompareVariantInline(admin.TabularInline):
    model = CompareVariant
    extra = 0
    readonly_fields = [field.name for field in CompareVariant._meta.fields]
    can_delete = False


@admin.register(CompareRun)
class CompareRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "conversation",
        "state",
        "expected_max_rub",
        "actual_cost_rub",
        "created_at",
    )
    list_filter = ("state",)
    readonly_fields = [field.name for field in CompareRun._meta.fields]
    inlines = [CompareVariantInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {"GET", "HEAD", "OPTIONS"}

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RoutingDecision)
class RoutingDecisionAdmin(admin.ModelAdmin):
    list_display = ("generation", "mode", "task_taxonomy", "selected_model", "created_at")
    list_filter = ("mode", "task_taxonomy")
    readonly_fields = [field.name for field in RoutingDecision._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {"GET", "HEAD", "OPTIONS"}

    def has_delete_permission(self, request, obj=None):
        return False
