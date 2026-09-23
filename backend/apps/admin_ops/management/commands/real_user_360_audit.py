from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.billing.models import BalanceReservation, LedgerEntry, RequestCost, Wallet
from apps.billing.services import reconstruct, reconstruct_buckets
from apps.chat.models import Generation, Message
from apps.payments.models import Payment, Refund


ZERO = Decimal("0.0000")


class Command(BaseCommand):
    help = (
        "Production 360 audit for real-user chat and money invariants: wallet, ledger, "
        "reservations, payments, refunds and generation settlement. Read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--stale-seconds",
            type=int,
            default=max(60, int(getattr(settings, "OPERATION_STALE_TIMEOUT_SECONDS", 900))),
            help="Age after which an ACTIVE reservation is considered stuck",
        )
        parser.add_argument(
            "--show",
            type=int,
            default=20,
            help="Maximum IDs printed for each problem class",
        )

    def handle(self, *args, **options):
        stale_seconds = max(60, int(options["stale_seconds"]))
        show = max(1, min(100, int(options["show"])))
        failures = []
        warnings = []

        def fail(code, detail):
            failures.append((code, detail))
            self.stdout.write(self.style.ERROR(f"[FAIL] {code}: {detail}"))

        def warn(code, detail):
            warnings.append((code, detail))
            self.stdout.write(self.style.WARNING(f"[WARN] {code}: {detail}"))

        def ok(code, detail):
            self.stdout.write(self.style.SUCCESS(f"[OK] {code}: {detail}"))

        self.stdout.write("=== REAL USER / MONEY AUDIT 360 ===")

        # Wallet and immutable-ledger reconciliation.
        bad_wallets = []
        for wallet in Wallet.objects.all().iterator():
            ledger_available, ledger_reserved = reconstruct(wallet)
            ledger_paid, ledger_promo = reconstruct_buckets(wallet)
            problems = []
            if wallet.available_rub != wallet.paid_rub + wallet.promo_rub:
                problems.append("available != paid + promo")
            if wallet.available_rub < ZERO or wallet.reserved_rub < ZERO or wallet.paid_rub < ZERO or wallet.promo_rub < ZERO:
                problems.append("negative wallet bucket")
            if (ledger_available, ledger_reserved) != (wallet.available_rub, wallet.reserved_rub):
                problems.append("ledger available/reserved mismatch")
            if (ledger_paid, ledger_promo) != (wallet.paid_rub, wallet.promo_rub):
                problems.append("ledger paid/promo mismatch")
            if problems:
                bad_wallets.append((str(wallet.id), ", ".join(problems)))
        if bad_wallets:
            fail("wallet_ledger", f"{len(bad_wallets)} wallet(s): {bad_wallets[:show]}")
        else:
            ok("wallet_ledger", f"{Wallet.objects.count()} wallet(s) reconcile exactly")

        # Reservations must never remain active after the operation is stale.
        cutoff = timezone.now() - timedelta(seconds=stale_seconds)
        stale = list(
            BalanceReservation.objects.filter(
                state=BalanceReservation.State.ACTIVE,
                created_at__lt=cutoff,
            ).values_list("id", flat=True)[:show]
        )
        stale_count = BalanceReservation.objects.filter(
            state=BalanceReservation.State.ACTIVE,
            created_at__lt=cutoff,
        ).count()
        if stale_count:
            fail("stale_reservations", f"{stale_count} stuck reservation(s), sample={list(map(str, stale))}")
        else:
            ok("stale_reservations", f"none older than {stale_seconds}s")

        # Completed generations must be settled once and equal their RequestCost.
        bad_completed = []
        completed = Generation.objects.filter(state=Generation.State.COMPLETED).select_related("assistant_message")
        for generation in completed.iterator():
            problems = []
            cost = RequestCost.objects.filter(generation_id=generation.id).first()
            reservation = (
                BalanceReservation.objects.filter(pk=generation.reservation_id).first()
                if generation.reservation_id else None
            )
            if generation.actual_cost_rub is None:
                problems.append("actual_cost missing")
            if cost is None:
                problems.append("RequestCost missing")
            elif cost.charged_rub != generation.actual_cost_rub:
                problems.append("RequestCost != generation actual")
            if reservation is None:
                problems.append("reservation missing")
            elif reservation.state != BalanceReservation.State.SETTLED:
                problems.append(f"reservation state={reservation.state}")
            elif reservation.actual_rub != generation.actual_cost_rub:
                problems.append("reservation actual != generation actual")
            if generation.assistant_message.status != Message.Status.COMPLETED:
                problems.append(f"assistant status={generation.assistant_message.status}")
            if problems:
                bad_completed.append((str(generation.id), ", ".join(problems)))
        if bad_completed:
            fail("completed_generation_settlement", f"{len(bad_completed)} generation(s): {bad_completed[:show]}")
        else:
            ok("completed_generation_settlement", f"{completed.count()} completed generation(s) settled consistently")

        # Failed/cancelled requests must not retain an ACTIVE customer reservation.
        terminal_active = []
        terminal = Generation.objects.filter(
            state__in=[Generation.State.FAILED, Generation.State.CANCELLED],
            reservation_id__isnull=False,
        )
        for generation in terminal.iterator():
            reservation = BalanceReservation.objects.filter(pk=generation.reservation_id).first()
            if reservation and reservation.state == BalanceReservation.State.ACTIVE:
                terminal_active.append(str(generation.id))
        if terminal_active:
            fail("terminal_active_reservation", f"{len(terminal_active)} terminal generation(s), sample={terminal_active[:show]}")
        else:
            ok("terminal_active_reservation", "failed/cancelled generations have no active holds")

        # A successful payment must credit the wallet exactly once.
        bad_payments = []
        successful = Payment.objects.filter(status=Payment.Status.SUCCEEDED)
        for payment in successful.iterator():
            key = f"credit:payment:{payment.id}"
            credit_count = LedgerEntry.objects.filter(idempotency_key=key).count()
            problems = []
            if payment.credited_at is None:
                problems.append("credited_at missing")
            if credit_count != 1:
                problems.append(f"wallet credits={credit_count}")
            if problems:
                bad_payments.append((str(payment.id), ", ".join(problems)))
        if bad_payments:
            fail("payment_credit", f"{len(bad_payments)} payment(s): {bad_payments[:show]}")
        else:
            ok("payment_credit", f"{successful.count()} successful payment(s) credited exactly once")

        bad_refunds = []
        succeeded_refunds = Refund.objects.filter(status=Refund.Status.SUCCEEDED)
        for refund in succeeded_refunds.iterator():
            problems = []
            if refund.wallet_debited_at is None:
                problems.append("wallet hold/debit missing")
            if refund.amount_rub <= 0:
                problems.append("non-positive amount")
            if problems:
                bad_refunds.append((str(refund.id), ", ".join(problems)))
        if bad_refunds:
            fail("refund_settlement", f"{len(bad_refunds)} refund(s): {bad_refunds[:show]}")
        else:
            ok("refund_settlement", f"{succeeded_refunds.count()} successful refund(s) have wallet debit")

        # Duplicate-looking human sends are warnings, not automatic deletions: users
        # are allowed to intentionally repeat a prompt. This detects the UI race in
        # production without corrupting legitimate history.
        suspicious = []
        recent_messages = list(
            Message.objects.filter(role=Message.Role.USER)
            .exclude(content="")
            .select_related("conversation")
            .order_by("conversation_id", "created_at", "id")
        )
        previous = None
        for message in recent_messages:
            if previous and previous.conversation_id == message.conversation_id and previous.content == message.content:
                delta = (message.created_at - previous.created_at).total_seconds()
                if 0 <= delta <= 2.0:
                    suspicious.append((str(message.conversation_id), str(previous.id), str(message.id), round(delta, 3)))
                    if len(suspicious) >= show:
                        break
            previous = message
        if suspicious:
            warn("rapid_duplicate_prompts", f"possible double-submit sample={suspicious}")
        else:
            ok("rapid_duplicate_prompts", "no same-prompt pairs within 2 seconds in scanned history")

        self.stdout.write(
            f"--- SUMMARY ---\ncritical={len(failures)} warnings={len(warnings)} "
            f"wallets={Wallet.objects.count()} generations={Generation.objects.count()} payments={Payment.objects.count()}"
        )
        if failures:
            raise CommandError(f"Real-user 360 audit failed: {len(failures)} critical invariant class(es)")
        self.stdout.write(self.style.SUCCESS("Real-user 360 audit passed: chat settlement and money invariants are consistent"))
