from django.contrib import admin

from .models import AIModel, Provider

admin.site.register(Provider)
admin.site.register(AIModel)
