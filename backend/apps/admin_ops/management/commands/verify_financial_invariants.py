from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q, Sum

from apps.billing.models import BalanceReservation, RequestCost, Wallet
from apps.chat.models import Generation
from apps.payments.models import Payment

ZERO = Decimal("0.0000")


class Command(BaseCommand):
    help = "Verify billing/payment invariants after load or chaos tests"

    def handle(self, *args, **options):
        failures = []

        negative_wallets = Wallet.objects.filter(
            Q(available_rub__lt=0)
            | Q(reserved_rub__lt=0)
            | Q(paid_rub__lt=0)
            | Q(promo_rub__lt=0)
        ).count()
        if negative_wallets:
            failures.append(f"negative_wallets={negative_wallets}")

        bucket_mismatch = Wallet.objects.exclude(
            available_rub=F("paid_rub") + F("promo_rub")
        ).count()
        if bucket_mismatch:
            failures.append(f"wallet_bucket_mismatch={bucket_mismatch}")

        invalid_active_reservations = BalanceReservation.objects.filter(
            state=BalanceReservation.State.ACTIVE
        ).filter(
            Q(amount_rub__lte=0)
            | ~Q(amount_rub=F("paid_amount_rub") + F("promo_amount_rub"))
        ).count()
        if invalid_active_reservations:
            failures.append(
                f"invalid_active_reservations={invalid_active_reservations}"
            )

        active_totals = {
            row["wallet_id"]: row["total"] or ZERO
            for row in BalanceReservation.objects.filter(
                state=BalanceReservation.State.ACTIVE
            )
            .values("wallet_id")
            .annotate(total=Sum("amount_rub"))
        }
        reservation_mismatch = 0
        for wallet in Wallet.objects.only("id", "reserved_rub").iterator():
            if wallet.reserved_rub != active_totals.get(wallet.id, ZERO):
                reservation_mismatch += 1
        if reservation_mismatch:
            failures.append(f"wallet_reservation_mismatch={reservation_mismatch}")

        completed_missing_cost = Generation.objects.filter(
            state=Generation.State.COMPLETED, actual_cost_rub__isnull=True
        ).count()
        if completed_missing_cost:
            failures.append(f"completed_missing_cost={completed_missing_cost}")

        generation_costs = {
            item.id: item.actual_cost_rub
            for item in Generation.objects.filter(
                id__in=RequestCost.objects.filter(charged_rub__isnull=False).values(
                    "generation_id"
                )
            ).only("id", "actual_cost_rub")
        }
        mismatch = 0
        for cost in RequestCost.objects.filter(charged_rub__isnull=False).only(
            "generation_id", "charged_rub"
        ):
            generation_cost = generation_costs.get(cost.generation_id)
            if generation_cost is not None and generation_cost != cost.charged_rub:
                mismatch += 1
        if mismatch:
            failures.append(f"generation_request_cost_mismatch={mismatch}")

        succeeded_uncredited = Payment.objects.filter(
            status=Payment.Status.SUCCEEDED, credited_at__isnull=True
        ).count()
        if succeeded_uncredited:
            failures.append(f"succeeded_uncredited={succeeded_uncredited}")

        if failures:
            for item in failures:
                self.stdout.write(self.style.ERROR(item))
            raise CommandError("Financial invariants failed")
        self.stdout.write(self.style.SUCCESS("Financial invariants passed"))
