from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, register


@register()
def chat_production_configuration_check(app_configs, **kwargs):
    """Fail production checks when cross-process chat coordination is not shared."""
    if settings.DEBUG:
        return []
    backend = str((settings.CACHES.get("default") or {}).get("BACKEND") or "").casefold()
    if "locmem" in backend:
        return [
            Error(
                "Production chat requires a shared cache (Redis); LocMem breaks cross-worker locks/recovery.",
                hint="Set CACHE_URL to the password-protected production Redis database.",
                id="chat.E001",
            )
        ]
    return []


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.chat"

    def ready(self):
        # UI-only organization models are registered here to keep inference models focused.
        from . import (
            asgi_stream,
            billing_recovery,
            cache_safety,
            compare,
            compare_readiness,
            context,
            context_safety,
            conversation_snapshot,
            cooperative_cancel,
            cost_preview,
            customer_capacity,
            error_contract,
            execution_fence,
            freshness_policy,
            live_tools,
            managed_stream,
            manual_selection_recovery,
            preflight_terminal,
            procurement_execution,
            public_error_wiring,
            response_safety,
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
        # Memory, summaries, retrieved history and files are reference data, never
        # system instructions. Keep a strict trusted allowlist before web enrichment
        # adds its own separately marked untrusted context.
        context_safety.install(context_module=context, streaming_module=streaming)

        # Install guards before terminal recovery. Customer traffic must never probe a
        # stale provider/model candidate, one conversation must never create two
        # concurrent generations, and a client Stop must cooperatively terminate the
        # same generation without creating another provider request.
        billing_recovery.install()
        manual_selection_recovery.install(serializers)
        # Compare is a customer-facing sibling of normal chat. It must never accept a
        # quarantined or commercially-unready model merely because the provider itself
        # is healthy.
        compare_readiness.install(compare)
        runtime_readiness.install(streaming)
        # Provider funds are reserved during prepare()/RequestCost. Once this exact
        # generation owns that reservation, execution must not require a second free
        # balance check and accidentally block itself. New fallback candidates remain
        # fail-closed until they can create their own provider reservation.
        procurement_execution.install(streaming)
        # Preview and prepare must use the same customer-funding eligibility. A more
        # expensive fallback must never make an affordable primary route unavailable;
        # final transactional reserve() remains the authority for races.
        customer_capacity.install(
            streaming_module=streaming,
            cost_preview_module=cost_preview,
        )
        # A provider that formally completes without useful text is not a successful
        # customer answer. Reject it locally and continue the existing fallback chain
        # without degrading an otherwise healthy key/provider.
        response_safety.install(streaming)
        # Recovery revokes the durable GenerationAttempt lease before touching money.
        # A provider thread that wakes up afterwards is fenced locally and can no
        # longer overwrite the recovered terminal state or settle twice.
        execution_fence.install(streaming)
        # Refresh the HTTP-view Conversation object after preview/confirmation and
        # immediately before the real transactional prepare. The raw prepare still
        # owns the final select_for_update and route decision.
        conversation_snapshot.install(streaming)
        single_flight.install(streaming)
        # Keep preflight terminalization outside single-flight: failures inside the
        # real prepare pipeline and bounded concurrency rejection share one durable
        # terminal-state contract. If no Generation exists this wrapper is a no-op.
        preflight_terminal.install(streaming)
        cooperative_cancel.install(streaming)
        terminal_recovery.install(streaming)
        # Financially accurate public wording sits outside terminal recovery.
        error_contract.install(streaming)
        # Live/reconnect/history must classify the same internal failure identically.
        public_error_wiring.install(asgi_stream)
        # Transport claim safety protects a simultaneous reconnect that loses the
        # atomic QUEUED->RUNNING claim from terminalizing the real producer.
        run_claim_safety.install(streaming, managed_stream)
        # The execution fence must be the final business-runtime wrapper: a worker
        # whose durable attempt lease was revoked by stale recovery may not enter
        # retry/fallback and may not surface as a new provider failure.
        execution_fence.install_outer_guard(streaming, managed_stream)
