import traceback
import uuid

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.billing.models import Wallet
from apps.chat.cost_preview import chat_cost_preview
from apps.chat.models import Conversation


class Command(BaseCommand):
    help = (
        "Check the real client AUTO chat preflight for funded users without calling an AI provider "
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
        parser.add_argument("--traceback", action="store_true")

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
                # Use a throwaway conversation inside a rolled-back transaction so this
                # exercises the same Router/pricing/wallet/spend-guard preflight as the
                # client endpoint without leaving messages, reservations or chat rows.
                with transaction.atomic():
                    conversation = Conversation.objects.create(
                        owner=user,
                        title="runtime diagnostic",
                        selected_model="echo-v1",
                        routing_mode=options["mode"],
                        memory_enabled=False,
                    )
                    preview = chat_cost_preview(
                        user=user,
                        conversation=conversation,
                        content="Привет. Ответь коротко.",
                        file_ids=[],
                    )
                    transaction.set_rollback(True)
                self.stdout.write(
                    self.style.SUCCESS(
                        "PREVIEW_OK "
                        f"selected_model={preview['selected_model']} "
                        f"estimated_min={preview['estimated_min_rub']} "
                        f"estimated_max={preview['estimated_max_rub']} "
                        f"confirmation_required={preview['confirmation_required']} "
                        f"spend_guard_blocked={preview.get('blocked_by_spend_guard', False)}"
                    )
                )
                if preview.get("blocked_by_spend_guard"):
                    self.stdout.write(
                        self.style.WARNING(
                            f"SPEND_GUARD {preview.get('spend_guard_message') or preview.get('spend_guard')}"
                        )
                    )
            except Exception as exc:
                failures += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"PREVIEW_FAILED type={type(exc).__name__} detail={exc}"
                    )
                )
                if options["traceback"]:
                    self.stderr.write(traceback.format_exc())

        self.stdout.write(f"CHECKED={len(users)} FAILURES={failures}")
        if failures:
            self.stderr.write("CLIENT_CHAT_PREFLIGHT_BROKEN")
