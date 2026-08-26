from django.contrib import admin

from .models import APIKey, APIUsage, AuditLog, Organization, OrganizationMembership

admin.site.register(Organization)
admin.site.register(OrganizationMembership)
admin.site.register(APIKey)
admin.site.register(APIUsage)
admin.site.register(AuditLog)
