from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.chat"

    def ready(self):
        # UI-only organization models are registered here to keep inference models focused.
        from . import (
            billing_recovery,
            runtime_readiness,
            signals,
            single_flight,
            streaming,
            terminal_recovery,
            ux_models,
        )  # noqa: F401

        # Install guards before terminal recovery. Customer traffic must never probe a
        # stale provider/model candidate, and one conversation must never create two
        # concurrent generations through double-click/multi-tab races.
        billing_recovery.install()
        runtime_readiness.install(streaming)
        single_flight.install(streaming)
        terminal_recovery.install(streaming)
