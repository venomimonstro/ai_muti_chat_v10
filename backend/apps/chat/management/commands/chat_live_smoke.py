from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.billing.models import BalanceReservation, RequestCost
from apps.billing.services import credit
from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import model_client_ready
from apps.chat.cost_preview import chat_cost_preview
from apps.chat.asgi_stream import managed_run_async
from apps.chat.models import Conversation, Generation
from apps.chat.streaming import prepare
from apps.procurement.models import ProviderSpendReservation


def _event(chunk: str):
    if not isinstance(chunk, str) or not chunk.startswith("event:"):
        return "", {}
    lines = chunk.splitlines()
    name = lines[0].split(":", 1)[1].strip() if ":" in lines[0] else ""
    payload = {}
    for line in lines[1:]:
        if not line.startswith("data:"):
            continue
        try:
            parsed = json.loads(line[5:].strip())
            payload = parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        break
    return name, payload


class Command(BaseCommand):
    help = (
        "Run one real customer chat generation through routing, wallet/provider "
        "reservations, external provider streaming, settlement and terminal state. "
        "All local test rows are rolled back."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=["auto", "economy", "balanced", "maximum", "manual"],
            default="auto",
        )
        parser.add_argument("--model", default="")
        parser.add_argument(
            "--prompt",
            default="Ответь одним коротким предложением: чат работает.",
        )

    def handle(self, *args, **options):
        mode = options["mode"]
        model_slug = str(options.get("model") or "").strip()
        if mode == "manual" and not model_slug:
            model = next(
                (
                    item
                    for item in AIModel.objects.filter(enabled=True)
                    .select_related("provider", "current_version")
                    .order_by("provider__priority", "slug")
                    if model_client_ready(item)
                ),
                None,
            )
            if model is None:
                raise CommandError("Нет client-ready модели для manual smoke")
            model_slug = model.slug

        marker = uuid.uuid4().hex
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=f"live-chat-smoke-{marker[:16]}",
                    email=f"live-chat-smoke-{marker}@example.test",
                    password=uuid.uuid4().hex,
                )
                credit(
                    user,
                    Decimal("1000.0000"),
                    "live-chat-smoke",
                    marker,
                    bucket="promo",
                )
                conversation = Conversation.objects.create(
                    owner=user,
                    title="live customer chat smoke",
                    selected_model=model_slug or "echo-v1",
                    routing_mode=mode,
                    memory_enabled=False,
                )
                prompt = str(options["prompt"] or "").strip()
                preview = chat_cost_preview(
                    user=user,
                    conversation=conversation,
                    content=prompt,
                    file_ids=[],
                )
                generation, created = prepare(
                    user=user,
                    conversation=conversation,
                    content=prompt,
                    client_message_id=uuid.uuid4(),
                    idempotency_key=f"live-chat-smoke:{marker}",
                    file_ids=[],
                )
                if not created:
                    raise RuntimeError("generation replayed unexpectedly")

                async def consume_customer_stream():
                    seen = []
                    deltas = 0
                    terminal = ""
                    terminal_payload = {}
                    async for chunk in managed_run_async(
                        generation,
                        heartbeat_seconds=1.0,
                    ):
                        name, payload = _event(chunk)
                        if not name or name == "heartbeat":
                            continue
                        seen.append(name)
                        if name == "delta":
                            deltas += 1
                        if name in {"completed", "cancelled", "error"}:
                            terminal = name
                            terminal_payload = payload
                    return seen, deltas, terminal, terminal_payload

                seen, deltas, terminal, terminal_payload = asyncio.run(
                    consume_customer_stream()
                )

                generation.refresh_from_db()
                request_cost = RequestCost.objects.get(generation_id=generation.id)
                reservation = BalanceReservation.objects.get(pk=generation.reservation_id)
                active_provider_reservations = ProviderSpendReservation.objects.filter(
                    source_key__startswith=f"chat:{request_cost.id}:",
                    state=ProviderSpendReservation.State.ACTIVE,
                ).count()

                if terminal != "completed":
                    raise RuntimeError(
                        f"terminal={terminal or '-'} payload={terminal_payload}"
                    )
                if generation.state != Generation.State.COMPLETED:
                    raise RuntimeError(f"generation_state={generation.state}")
                if not str(generation.assistant_message.content or "").strip():
                    raise RuntimeError("assistant response is empty")
                if request_cost.provider_cost_rub is None:
                    raise RuntimeError("provider cost was not checkpointed")
                if request_cost.charged_rub is None:
                    raise RuntimeError("customer charge was not settled")
                if reservation.state != BalanceReservation.State.SETTLED:
                    raise RuntimeError(
                        f"customer reservation state={reservation.state}"
                    )
                if active_provider_reservations:
                    raise RuntimeError(
                        f"active provider reservations={active_provider_reservations}"
                    )

                self.stdout.write(
                    self.style.SUCCESS(
                        "CHAT_LIVE_SMOKE_OK "
                        f"mode={mode} "
                        f"provider={generation.provider_slug} "
                        f"model={generation.routed_model or generation.model} "
                        f"events={','.join(seen)} "
                        f"deltas={deltas} "
                        f"input_tokens={generation.input_tokens} "
                        f"output_tokens={generation.output_tokens} "
                        f"charge_rub={request_cost.charged_rub}"
                    )
                )
                transaction.set_rollback(True)
        except Exception as exc:
            raise CommandError(
                f"CHAT_LIVE_SMOKE_BROKEN type={type(exc).__name__} detail={exc}"
            ) from exc
