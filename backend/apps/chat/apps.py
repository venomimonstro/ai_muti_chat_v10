from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.chat"

    def ready(self):
        # Register UI-only chat organization models without coupling them to the
        # inference/billing models module.
        from . import ux_models  # noqa: F401
