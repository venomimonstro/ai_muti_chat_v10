import uuid
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.billing.models import BalanceReservation
from apps.billing.services import credit
from apps.chat.cost_preview import chat_cost_preview
from apps.chat.models import Conversation, Generation
from apps.chat.streaming import prepare


class Command(BaseCommand):
    help = (
        "Run the real customer AUTO chat preflight (user, wallet, router, pricing, reservation, generation) "
        "inside a rollback-only transaction. Does not call an external AI provider and leaves no test data."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=["economy", "balanced", "maximum"],
            default="balanced",
        )

    def handle(self, *args, **options):
        mode = options["mode"]
        marker = uuid.uuid4().hex
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=f"runtime-smoke-{marker[:16]}",
                    email=f"runtime-smoke-{marker}@example.test",
                    password=uuid.uuid4().hex,
                )
                credit(
                    user,
                    Decimal("10.0000"),
                    "runtime-smoke",
                    marker,
                    bucket="promo",
                )
                conversation = Conversation.objects.create(
                    owner=user,
                    title="runtime smoke",
                    selected_model="echo-v1",
                    routing_mode=mode,
                    memory_enabled=False,
                )
                content = "Привет. Ответь коротко."
                preview = chat_cost_preview(
                    user=user,
                    conversation=conversation,
                    content=content,
                    file_ids=[],
                )
                generation, created = prepare(
                    user=user,
                    conversation=conversation,
                    content=content,
                    client_message_id=uuid.uuid4(),
                    idempotency_key=f"runtime-smoke:{marker}",
                    file_ids=[],
                )
                generation.refresh_from_db()
                if not created:
                    raise RuntimeError("synthetic generation was unexpectedly replayed")
                if generation.state != Generation.State.QUEUED:
                    raise RuntimeError(f"generation state is {generation.state}, expected queued")
                if not generation.reservation_id:
                    raise RuntimeError("generation has no balance reservation")
                reservation = BalanceReservation.objects.get(pk=generation.reservation_id)
                decision = generation.routing_decision
                if not decision.selected_model_id:
                    raise RuntimeError("router did not select a model")
                if preview.get("blocked_by_spend_guard"):
                    raise RuntimeError(f"preview blocked by spend guard: {preview.get('spend_guard_message')}")
                self.stdout.write(
                    self.style.SUCCESS(
                        "CHAT_PREFLIGHT_OK "
                        f"mode={mode} "
                        f"provider={decision.selected_model.provider.slug} "
                        f"model={decision.selected_model.slug} "
                        f"reservation={reservation.amount_rub} "
                        f"preview_max={preview.get('estimated_max_rub')}"
                    )
                )
                transaction.set_rollback(True)
        except Exception as exc:
            raise CommandError(
                f"CHAT_PREFLIGHT_BROKEN type={type(exc).__name__} detail={exc}"
            ) from exc
