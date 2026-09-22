import traceback
import uuid

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.billing.models import BalanceReservation, Wallet
from apps.chat.cost_preview import chat_cost_preview
from apps.chat.models import Conversation
from apps.chat.streaming import prepare


class Command(BaseCommand):
    help = (
        "Check the real client AUTO chat preview and reservation preflight without calling an AI provider "
        "or permanently reserving/spending wallet funds."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            default="",
            help="Username, email or UUID. If omitted, checks up to 20 active funded non-platform-admin users.",
        )
        parser.add_argument(
            "--mode",
            choices=["economy", "balanced", "maximum"],
            default="economy",
        )
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="Use a new empty conversation instead of the user's most recently updated conversation.",
        )
        # BaseCommand already provides the standard --traceback option.

    def _users(self, identifier):
        queryset = User.objects.filter(status=User.Status.ACTIVE).exclude(
            role=User.Role.PLATFORM_ADMIN
        )
        if identifier:
            try:
                parsed = uuid.UUID(identifier)
            except (TypeError, ValueError, AttributeError):
                parsed = None
            if parsed is not None:
                return queryset.filter(pk=parsed)
            return queryset.filter(username=identifier) | queryset.filter(email=identifier)
        return queryset.filter(wallet__available_rub__gt=0).order_by("-date_joined")[:20]

    def handle(self, *args, **options):
        users = list(self._users(options["user"]))
        if not users:
            self.stderr.write("NO_MATCHING_FUNDED_CLIENT_USERS")
            return

        failures = 0
        for user in users:
            wallet = Wallet.objects.filter(user=user).first()
            available = wallet.available_rub if wallet else 0
            reserved = wallet.reserved_rub if wallet else 0
            self.stdout.write(
                f"USER username={user.username} id={user.id} role={user.role} "
                f"available={available} reserved={reserved} mode={options['mode']}"
            )
            try:
                # Everything below is rolled back. prepare() exercises the same route,
                # context assembly, pricing and wallet reservation as /messages/stream/
                # but managed_run() is never called, so no provider request is made.
                with transaction.atomic():
                    conversation = None
                    if not options.get("fresh"):
                        conversation = (
                            Conversation.objects.filter(owner=user)
                            .order_by("-updated_at", "-created_at")
                            .first()
                        )
                    if conversation is None:
                        conversation = Conversation.objects.create(
                            owner=user,
                            title="runtime diagnostic",
                            selected_model="echo-v1",
                            routing_mode=options["mode"],
                            memory_enabled=False,
                        )
                    else:
                        conversation.routing_mode = options["mode"]
                        conversation.save(update_fields=["routing_mode"])

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
                        idempotency_key=f"diagnostic-{uuid.uuid4()}",
                        file_ids=[],
                    )
                    reservation_amount = None
                    if generation.reservation_id:
                        reservation_amount = BalanceReservation.objects.get(
                            pk=generation.reservation_id
                        ).amount_rub
                    threshold = preview["confirmation_threshold_rub"]
                    mismatch = bool(
                        reservation_amount is not None
                        and reservation_amount >= threshold
                        and not preview["confirmation_required"]
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            "PREFLIGHT_OK "
                            f"conversation={conversation.id} "
                            f"selected_model={preview['selected_model']} "
                            f"estimated_min={preview['estimated_min_rub']} "
                            f"estimated_max={preview['estimated_max_rub']} "
                            f"reservation={reservation_amount} "
                            f"confirmation_required={preview['confirmation_required']} "
                            f"confirmation_mismatch={mismatch} "
                            f"spend_guard_blocked={preview.get('blocked_by_spend_guard', False)} "
                            f"generation_created={created}"
                        )
                    )
                    if mismatch:
                        failures += 1
                        self.stderr.write(
                            self.style.ERROR(
                                "CONFIRMATION_MISMATCH preview did not request confirmation but real reservation crossed threshold"
                            )
                        )
                    if preview.get("blocked_by_spend_guard"):
                        self.stdout.write(
                            self.style.WARNING(
                                f"SPEND_GUARD {preview.get('spend_guard_message') or preview.get('spend_guard')}"
                            )
                        )
                    transaction.set_rollback(True)
            except Exception as exc:
                failures += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"PREFLIGHT_FAILED type={type(exc).__name__} detail={exc}"
                    )
                )
                if options.get("traceback"):
                    self.stderr.write(traceback.format_exc())

        self.stdout.write(f"CHECKED={len(users)} FAILURES={failures}")
        if failures:
            self.stderr.write("CLIENT_CHAT_PREFLIGHT_BROKEN")
