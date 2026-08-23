from django.contrib import admin

from .models import AIModel, Provider, ProviderHealthSnapshot, ReliabilityIncident

admin.site.register(Provider)
admin.site.register(AIModel)
admin.site.register(ProviderHealthSnapshot)
admin.site.register(ReliabilityIncident)
