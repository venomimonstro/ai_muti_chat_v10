from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.chat"

    def ready(self):
        # UI-only organization models are registered here to keep inference models focused.
        from . import signals, ux_models  # noqa: F401

        # Compatibility activation: existing views/asgi modules keep importing
        # apps.chat.managed_stream, while production execution uses the v2 wrapper
        # that permits failover only inside the pre-priced/pre-reserved route.
        from . import managed_stream, managed_stream_v2

        managed_stream.managed_run = managed_stream_v2.managed_run
