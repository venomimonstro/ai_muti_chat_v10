from django.contrib import admin

from .models import AdminAuditEvent, BackupRecord, FeatureFlag, ReleaseRecord, SecurityEvent


@admin.register(AdminAuditEvent)
class AdminAuditEventAdmin(admin.ModelAdmin):
    list_display = ("action", "target_type", "target_id", "actor", "created_at")
    list_filter = ("action", "target_type")
    search_fields = ("target_id", "actor__email")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(FeatureFlag)
admin.site.register(SecurityEvent)
admin.site.register(ReleaseRecord)
admin.site.register(BackupRecord)
