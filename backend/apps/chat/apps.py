from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.chat"

    def ready(self):
        # UI-only organization models are registered here to keep inference models focused.
        from . import billing_recovery, signals, streaming, terminal_recovery, ux_models  # noqa: F401

        # Install the billing guard before terminal recovery. A confirmed upstream
        # result must settle the authorized reserve (capped) before the terminal
        # wrapper can safely convert an over-reserve failure into COMPLETED.
        billing_recovery.install()
        terminal_recovery.install(streaming)
