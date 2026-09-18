from django.apps import AppConfig


class ImageStudioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.image_studio"
    verbose_name = "Студия изображений"

    def ready(self):
        from . import signals  # noqa: F401
