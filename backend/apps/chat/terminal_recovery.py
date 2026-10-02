from __future__ import annotations

import json
import logging
import sys

from django.db import transaction
from django.utils import timezone

from apps.billing.models import BalanceReservation, RequestCost
from apps.billing.pricing import calculate, calculate_from_snapshot

from .models import Generation, Message

logger = logging.getLogger(__name__)


def _event_name(chunk):
    if not isinstance(chunk, str) or not chunk.startswith("event: "):
        return ""
    return chunk.splitlines()[0][7:].strip()


def _event_payload(chunk):
    if not isinstance(chunk, str):
        return {}
    for line in chunk.splitlines():
        if line.startswith("data: "):
            try:
                payload = json.loads(line[6:])
            except (TypeError, ValueError, json.JSONDecodeError):
                return {}
            return payload if isinstance(payload, dict) else {}
    return {}


def _persist_delivered_text(generation_id, delivered_text):
    text = str(delivered_text or "")
    if not text:
        return
    with transaction.atomic():
        generation = (
            Generation.objects.select_for_update()
            .select_related("assistant_message")
            .get(pk=generation_id)
        )
        assistant = generation.assistant_message
        # Only persist text that this wrapper observed in SSE delta events. Never
        # manufacture or recover hidden provider text that was not delivered.
        if len(text) >= len(assistant.content or ""):
            assistant.content = text
            assistant.status = Message.Status.STREAMING
            assistant.save(update_fields=["content", "status"])


def _confirmed_overrun_payload(generation_id):
    """Turn only a proven post-provider reserve overrun into a completed response."""
    with transaction.atomic():
        generation = (
            Generation.objects.select_for_update()
            .select_related("assistant_message")
            .get(pk=generation_id)
        )
        if (
            generation.state != Generation.State.FAILED
            or generation.error_code != "cost_or_internal_error"
            or not generation.reservation_id
        ):
            return None

        reservation = (
            BalanceReservation.objects.select_for_update()
            .filter(pk=generation.reservation_id)
            .first()
        )
        request_cost = (
            RequestCost.objects.select_for_update()
            .select_related("price_version")
            .filter(generation_id=generation.id)
            .first()
        )
        if reservation is None or request_cost is None:
            return None
        if reservation.state != BalanceReservation.State.SETTLED:
            return None
        if request_cost.provider_cost_rub is None or request_cost.charged_rub is None:
            return None
        if not (request_cost.input_tokens or request_cost.output_tokens):
            return None

        if request_cost.pricing_snapshot:
            _provider_cost, calculated_charge, _profit, _margin = calculate_from_snapshot(
                request_cost.price_version,
                request_cost.input_tokens,
                request_cost.output_tokens,
                request_cost.pricing_snapshot,
            )
        else:
            _provider_cost, calculated_charge = calculate(
                request_cost.price_version,
                request_cost.input_tokens,
                request_cost.output_tokens,
            )
        if calculated_charge <= reservation.amount_rub:
            return None
        if request_cost.charged_rub != reservation.amount_rub:
            return None

        assistant = generation.assistant_message
        if not assistant.content:
            return None

        assistant.status = Message.Status.COMPLETED
        assistant.save(update_fields=["status"])
        generation.state = Generation.State.COMPLETED
        generation.error_code = ""
        generation.actual_cost_rub = request_cost.charged_rub
        generation.input_tokens = request_cost.input_tokens
        generation.output_tokens = request_cost.output_tokens
        generation.completed_at = generation.completed_at or timezone.now()
        generation.save(
            update_fields=[
                "state",
                "error_code",
                "actual_cost_rub",
                "input_tokens",
                "output_tokens",
                "completed_at",
            ]
        )
        return {
            "state": "completed",
            "cost_rub": str(request_cost.charged_rub),
            "input_tokens": request_cost.input_tokens,
            "output_tokens": request_cost.output_tokens,
            "model": generation.routed_model or generation.model,
            "provider": generation.provider_slug,
            "recovered_from": "authorized_reserve_overrun",
        }


def _post_complete(streaming_module, generation_id):
    """Run the same non-financial completion hooks as the ordinary success path.

    These hooks are best-effort by design: a search-index or rolling-summary failure
    must never turn an already paid, fully delivered answer back into a user-visible
    chat failure.
    """
    try:
        generation = (
            Generation.objects.select_related("assistant_message", "user_message__conversation")
            .get(pk=generation_id)
        )
        streaming_module._index_history(generation.assistant_message)
        streaming_module.refresh_rolling_summary(generation.user_message.conversation)
    except Exception:
        logger.exception(
            "Recovered chat completion post-processing failed generation_id=%s",
            generation_id,
        )


def install(streaming_module) -> None:
    raw_run = streaming_module.run
    if getattr(raw_run, "_ai_workspace_terminal_recovery", False) is True:
        return

    def run(generation, *args, **kwargs):
        delivered_text = str(getattr(generation.assistant_message, "content", "") or "")
        for chunk in raw_run(generation, *args, **kwargs):
            event = _event_name(chunk)
            if event == "delta":
                delivered_text += str(_event_payload(chunk).get("text") or "")
            elif event == "error":
                # The core stream deliberately fails closed on reserve overrun. Before
                # proving and recovering that narrow state, persist the exact text the
                # client already received so short (< FLUSH_CHARS) answers are not lost.
                _persist_delivered_text(generation.id, delivered_text)
                payload = _confirmed_overrun_payload(generation.id)
                if payload is not None:
                    _post_complete(streaming_module, generation.id)
                    yield streaming_module.sse("completed", payload)
                    continue
            yield chunk

    run._ai_workspace_terminal_recovery = True
    run._raw_run = raw_run
    streaming_module.run = run

    # Several chat entrypoints import run by value before AppConfig.ready(). Keep
    # streaming and non-streaming APIs on the exact same final runtime chain.
    for module_name in ("apps.chat.managed_stream", "apps.chat.services", "apps.chat.views"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "run"):
            module.run = run
