from __future__ import annotations

import json
import sys

from django.db import transaction
from django.utils import timezone

from apps.billing.models import BalanceReservation, RequestCost
from apps.billing.pricing import calculate, calculate_from_snapshot

from .models import Generation, Message


def _event_name(chunk):
    if not isinstance(chunk, str) or not chunk.startswith("event: "):
        return ""
    return chunk.splitlines()[0][7:].strip()


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


def install(streaming_module) -> None:
    raw_run = streaming_module.run
    if getattr(raw_run, "_ai_workspace_terminal_recovery", False):
        return

    def run(generation, *args, **kwargs):
        for chunk in raw_run(generation, *args, **kwargs):
            if _event_name(chunk) == "error":
                payload = _confirmed_overrun_payload(generation.id)
                if payload is not None:
                    yield streaming_module.sse("completed", payload)
                    continue
            yield chunk

    run._ai_workspace_terminal_recovery = True
    run._raw_run = raw_run
    streaming_module.run = run

    # managed_stream imports run by value; rebind if it was imported before ready().
    managed_stream = sys.modules.get("apps.chat.managed_stream")
    if managed_stream is not None:
        managed_stream.run = run
