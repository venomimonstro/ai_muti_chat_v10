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
            error_contract,
            freshness_policy,
            live_tools,
            manual_selection_recovery,
            preflight_terminal,
            runtime_readiness,
            serializers,
            signals,
            single_flight,
            streaming,
            terminal_recovery,
            ux_models,
            web_context,
        )  # noqa: F401

        # Current public facts such as office holders, exchange rates and software
        # versions require web grounding even if the user does not say "today".
        freshness_policy.install(live_tools, web_context)

        # Install guards before terminal recovery. Customer traffic must never probe a
        # stale provider/model candidate, one conversation must never create two
        # concurrent generations, and a client Stop must cooperatively terminate the
        # same generation without creating another provider request.
        billing_recovery.install()
        manual_selection_recovery.install(serializers)
        runtime_readiness.install(streaming)
        single_flight.install(streaming)
        # Keep preflight terminalization outside single-flight: failures inside the
        # real prepare pipeline and bounded concurrency rejection share one durable
        # terminal-state contract. If no Generation exists this wrapper is a no-op.
        preflight_terminal.install(streaming)
        cooperative_cancel.install(streaming)
        terminal_recovery.install(streaming)
        # Keep this outermost so recovered terminal successes are never rewritten,
        # while genuine partial failures receive financially accurate public wording.
        error_contract.install(streaming)
