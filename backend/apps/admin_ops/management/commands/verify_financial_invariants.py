from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q

from apps.billing.models import BalanceReservation, RequestCost, Wallet
from apps.chat.models import Generation
from apps.payments.models import Payment


class Command(BaseCommand):
    help = "Verify billing/payment invariants after load or chaos tests"

    def handle(self, *args, **options):
        failures = []
        negative_wallets = Wallet.objects.filter(
            Q(available_rub__lt=0) | Q(reserved_rub__lt=0) | Q(paid_rub__lt=0) | Q(promo_rub__lt=0)
        ).count()
        if negative_wallets:
            failures.append(f"negative_wallets={negative_wallets}")

        over_reserved = Wallet.objects.filter(reserved_rub__gt=F("available_rub") + F("reserved_rub")).count()
        if over_reserved:
            failures.append(f"invalid_reservations={over_reserved}")

        completed_missing_cost = Generation.objects.filter(
            state=Generation.State.COMPLETED, actual_cost_rub__isnull=True
        ).count()
        if completed_missing_cost:
            failures.append(f"completed_missing_cost={completed_missing_cost}")

        request_mismatch = RequestCost.objects.filter(
            charged_rub__isnull=False,
            generation_id__in=Generation.objects.filter(state=Generation.State.COMPLETED).values("id"),
        ).exclude(charged_rub=F("generation_id__actual_cost_rub") if False else F("charged_rub")).count()
        # Cross-model equality is validated below without unsupported cross-table F joins.
        mismatch = 0
        for cost in RequestCost.objects.filter(charged_rub__isnull=False).iterator():
            generation = Generation.objects.filter(pk=cost.generation_id).only("actual_cost_rub").first()
            if generation and generation.actual_cost_rub != cost.charged_rub:
                mismatch += 1
        if mismatch:
            failures.append(f"generation_request_cost_mismatch={mismatch}")

        succeeded_uncredited = Payment.objects.filter(
            status=Payment.Status.SUCCEEDED, credited_at__isnull=True
        ).count()
        if succeeded_uncredited:
            failures.append(f"succeeded_uncredited={succeeded_uncredited}")

        active_without_amount = BalanceReservation.objects.filter(
            state=BalanceReservation.State.ACTIVE, amount_rub__lte=0
        ).count()
        if active_without_amount:
            failures.append(f"invalid_active_reservations={active_without_amount}")

        if failures:
            for item in failures:
                self.stdout.write(self.style.ERROR(item))
            raise CommandError("Financial invariants failed")
        self.stdout.write(self.style.SUCCESS("Financial invariants passed"))
