from django.contrib import admin

from .models import Conversation, Generation, GenerationAttempt, Message, RoutingDecision

admin.site.register(Conversation)
admin.site.register(Message)
admin.site.register(Generation)
admin.site.register(GenerationAttempt)


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
