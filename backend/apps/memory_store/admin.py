from django.contrib import admin

from .models import GenerationMemoryUsage, MemoryCandidate, MemoryItem, MemoryRevision

admin.site.register(MemoryItem)
admin.site.register(MemoryRevision)
admin.site.register(GenerationMemoryUsage)
admin.site.register(MemoryCandidate)
