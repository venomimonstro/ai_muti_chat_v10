from django.contrib import admin

from .models import Notification, SupportRequest, User, UserPreference

admin.site.register(User)
admin.site.register(UserPreference)
admin.site.register(Notification)
admin.site.register(SupportRequest)
