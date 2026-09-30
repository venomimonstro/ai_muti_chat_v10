from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.chat"

    def ready(self):
        # UI-only organization models are registered here to keep inference models focused.
        from . import (
            billing_recovery,
            cache_safety,
            cooperative_cancel,
            manual_selection_recovery,
            runtime_readiness,
            serializers,
            signals,
            single_flight,
            streaming,
            terminal_recovery,
            ux_models,
        )  # noqa: F401

        # Install guards before terminal recovery. Customer traffic must never probe a
        # stale provider/model candidate, one conversation must never create two
        # concurrent generations, and a client Stop must cooperatively terminate the
        # same generation without creating another provider request.
        billing_recovery.install()
        manual_selection_recovery.install(serializers)
        runtime_readiness.install(streaming)
        single_flight.install(streaming)
        cooperative_cancel.install(streaming)
        terminal_recovery.install(streaming)
