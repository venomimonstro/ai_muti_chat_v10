from django.contrib import admin  # noqa: I001

from .models import (
    GeneratedImage,
    ImageGeneration,
    ImageModel,
)


admin.site.register(ImageModel)


class GeneratedImageInline(admin.TabularInline):
    model = GeneratedImage
    extra = 0
    readonly_fields = [field.name for field in GeneratedImage._meta.fields]
    can_delete = False


@admin.register(ImageGeneration)
class ImageGenerationAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "model", "state", "actual_count", "actual_cost_rub", "created_at")
    list_filter = ("state", "model")
    readonly_fields = [field.name for field in ImageGeneration._meta.fields]
    inlines = [GeneratedImageInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
