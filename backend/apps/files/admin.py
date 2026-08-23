from django.contrib import admin

from .models import FileAsset, FileChunk, FileProcessingJob

admin.site.register(FileAsset)
admin.site.register(FileChunk)
admin.site.register(FileProcessingJob)
