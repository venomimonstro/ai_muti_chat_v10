from __future__ import annotations

import contextvars
import logging
import sys

from django.db import transaction

from apps.billing.models import RequestCost
from apps.billing.pricing import calculate, calculate_from_snapshot

logger = logging.getLogger(__name__)

_CURRENT_GENERATION_ID = contextvars.ContextVar("chat_provider_delivery_generation_id", default=None)
_raw_adapter_for = None


def _checkpoint(model, event):
    generation_id = _CURRENT_GENERATION_ID.get()
    if generation_id is None:
        return
    input_tokens = max(0, int(getattr(event, "input_tokens", 0) or 0))
    output_tokens = max(0, int(getattr(event, "output_tokens", 0) or 0))
    if not (input_tokens or output_tokens):
        return

    with transaction.atomic():
        request_cost = (
            RequestCost.objects.select_for_update()
            .select_related("price_version")
            .get(generation_id=generation_id)
        )
        # The route switches RequestCost.price_version immediately before calling
        # this adapter. Refuse to journal usage against a stale/mismatched model.
        if request_cost.price_version.model_slug != model.slug:
            raise RuntimeError("provider delivery checkpoint model mismatch")

        if request_cost.pricing_snapshot:
            provider_cost, _charge, _profit, _margin = calculate_from_snapshot(
                request_cost.price_version,
                input_tokens,
                output_tokens,
                request_cost.pricing_snapshot,
            )
        else:
            provider_cost, _charge = calculate(
                request_cost.price_version,
                input_tokens,
                output_tokens,
            )

        # Idempotent for repeated SDK completion callbacks. Saving provider_cost_rub
        # triggers the procurement signal, which settles the already reserved provider
        # spend before customer settlement is attempted by the main streaming runtime.
        request_cost.provider_cost_rub = provider_cost
        request_cost.input_tokens = input_tokens
        request_cost.output_tokens = output_tokens
        request_cost.save(
            update_fields=["provider_cost_rub", "input_tokens", "output_tokens"]
        )


def install(streaming_module) -> None:
    """Journal provider-confirmed usage before customer settlement.

    The customer can receive deltas before final billing. If the database/customer
    settlement fails after the provider has completed, the durable RequestCost and
    procurement spend are the proof used by release()/stale recovery to charge only
    confirmed usage instead of incorrectly issuing a full refund.
    """
    global _raw_adapter_for

    current_adapter_for = streaming_module.adapter_for
    if getattr(current_adapter_for, "_ai_workspace_delivery_checkpoint", False):
        return
    _raw_adapter_for = current_adapter_for

    class CheckpointAdapter:
        def __init__(self, inner, model):
            self.inner = inner
            self.model = model

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def stream(self, *args, **kwargs):
            for event in self.inner.stream(*args, **kwargs):
                if getattr(event, "kind", "") == "completed":
                    _checkpoint(self.model, event)
                yield event

    def checkpoint_adapter_for(model, *args, **kwargs):
        return CheckpointAdapter(_raw_adapter_for(model, *args, **kwargs), model)

    checkpoint_adapter_for._ai_workspace_delivery_checkpoint = True
    checkpoint_adapter_for._raw_adapter_for = current_adapter_for
    streaming_module.adapter_for = checkpoint_adapter_for

    raw_run = streaming_module.run
    if not getattr(raw_run, "_ai_workspace_delivery_checkpoint_context", False):
        def run(generation, *args, **kwargs):
            token = _CURRENT_GENERATION_ID.set(generation.id)
            try:
                yield from raw_run(generation, *args, **kwargs)
            finally:
                _CURRENT_GENERATION_ID.reset(token)

        run._ai_workspace_delivery_checkpoint_context = True
        run._raw_run = raw_run
        streaming_module.run = run

        for module_name in ("apps.chat.managed_stream", "apps.chat.services", "apps.chat.views"):
            module = sys.modules.get(module_name)
            if module is not None and hasattr(module, "run"):
                module.run = run

    for module_name in ("apps.chat.services", "apps.chat.views", "apps.chat.managed_stream"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "adapter_for"):
            module.adapter_for = checkpoint_adapter_for
