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
            execution_fence,
            freshness_policy,
            live_tools,
            managed_stream,
            manual_selection_recovery,
            preflight_terminal,
            procurement_execution,
            response_safety,
            retry_adapter,
            run_claim_safety,
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
        # Provider funds are reserved during prepare()/RequestCost. Once this exact
        # generation owns that reservation, execution must not require a second free
        # balance check and accidentally block itself. New fallback candidates remain
        # fail-closed until they can create their own provider reservation.
        procurement_execution.install(streaming)
        # A provider that formally completes without useful text is not a successful
        # customer answer. Reject it locally and continue the existing fallback chain
        # without degrading an otherwise healthy key/provider.
        response_safety.install(streaming)
        # Resolve the final adapter inside every retry attempt. A key degraded by the
        # previous attempt must never be reused merely because the provider still has
        # another healthy credential; dispatch gets a fresh chance to select it.
        retry_adapter.install(streaming)
        # Recovery revokes the durable GenerationAttempt lease before touching money.
        # A provider thread that wakes up afterwards is fenced locally and can no
        # longer overwrite the recovered terminal state or settle twice.
        execution_fence.install(streaming)
        single_flight.install(streaming)
        # Keep preflight terminalization outside single-flight: failures inside the
        # real prepare pipeline and bounded concurrency rejection share one durable
        # terminal-state contract. If no Generation exists this wrapper is a no-op.
        preflight_terminal.install(streaming)
        cooperative_cancel.install(streaming)
        terminal_recovery.install(streaming)
        # Financially accurate public wording sits outside terminal recovery.
        error_contract.install(streaming)
        # Transport claim safety is intentionally last: a simultaneous reconnect that
        # loses the atomic QUEUED->RUNNING claim is a follower, not an incomplete run,
        # and must never terminalize the real producer.
        run_claim_safety.install(streaming, managed_stream)

        # Production runs under Uvicorn/ASGI. Import the request bridge only after all
        # routing/billing/recovery wrappers above are installed so it captures the
        # authoritative final synchronous pipeline, then expose that pipeline through
        # a native async StreamingHttpResponse iterator instead of Django's sync-ASGI
        # adaptation/buffering path.
        from . import activity_stream, asgi_request_stream

        asgi_request_stream.install(activity_stream)
