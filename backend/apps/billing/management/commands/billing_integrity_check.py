from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from apps.billing.models import BalanceReservation, RequestCost, Wallet
from apps.billing.services import reconstruct, reconstruct_buckets

ZERO = Decimal("0.0000")


class Command(BaseCommand):
    help = "Audit wallet, ledger, reservation and settled-request invariants without mutating data."

    def handle(self, *args, **options):
        failures = 0
        checked_wallets = 0

        for wallet in Wallet.objects.select_related("user").iterator():
            checked_wallets += 1
            issues = []
            if wallet.available_rub != wallet.paid_rub + wallet.promo_rub:
                issues.append(
                    f"available_bucket_mismatch:{wallet.available_rub}!={wallet.paid_rub}+{wallet.promo_rub}"
                )

            ledger_available, ledger_reserved = reconstruct(wallet)
            ledger_paid, ledger_promo = reconstruct_buckets(wallet)
            if wallet.available_rub != ledger_available:
                issues.append(f"ledger_available_mismatch:{wallet.available_rub}!={ledger_available}")
            if wallet.reserved_rub != ledger_reserved:
                issues.append(f"ledger_reserved_mismatch:{wallet.reserved_rub}!={ledger_reserved}")
            if wallet.paid_rub != ledger_paid:
                issues.append(f"ledger_paid_mismatch:{wallet.paid_rub}!={ledger_paid}")
            if wallet.promo_rub != ledger_promo:
                issues.append(f"ledger_promo_mismatch:{wallet.promo_rub}!={ledger_promo}")

            active_reserved = (
                wallet.reservations.filter(state=BalanceReservation.State.ACTIVE).aggregate(
                    total=Sum("amount_rub")
                )["total"]
                or ZERO
            )
            if wallet.reserved_rub != active_reserved:
                issues.append(f"active_reserve_mismatch:{wallet.reserved_rub}!={active_reserved}")
            if min(wallet.available_rub, wallet.reserved_rub, wallet.paid_rub, wallet.promo_rub) < ZERO:
                issues.append("negative_wallet_component")

            if issues:
                failures += 1
                self.stdout.write(
                    self.style.ERROR(
                        "WALLET_FAIL "
                        f"user={wallet.user_id} wallet={wallet.id} "
                        + " reasons=" + ",".join(issues)
                    )
                )

        bad_reservations = 0
        for reservation in BalanceReservation.objects.iterator():
            issues = []
            if reservation.amount_rub != reservation.paid_amount_rub + reservation.promo_amount_rub:
                issues.append("bucket_mismatch")
            if reservation.actual_rub is not None and reservation.actual_rub > reservation.amount_rub:
                issues.append("actual_above_reserved")
            if reservation.state == BalanceReservation.State.ACTIVE and reservation.settled_at is not None:
                issues.append("active_with_settled_at")
            if reservation.state != BalanceReservation.State.ACTIVE and reservation.settled_at is None:
                issues.append("closed_without_settled_at")
            if issues:
                bad_reservations += 1
                failures += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"RESERVATION_FAIL id={reservation.id} reasons={','.join(issues)}"
                    )
                )

        bad_costs = 0
        for item in RequestCost.objects.exclude(charged_rub__isnull=True).iterator():
            issues = []
            if item.charged_rub < ZERO:
                issues.append("negative_charge")
            if item.provider_cost_rub is None and item.charged_rub > ZERO:
                issues.append("charged_without_confirmed_provider_cost")
            if item.charged_rub > item.estimated_rub:
                issues.append("charged_above_authorized_estimate")
            if issues:
                bad_costs += 1
                failures += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"REQUEST_COST_FAIL generation={item.generation_id} reasons={','.join(issues)}"
                    )
                )

        self.stdout.write(
            " ".join(
                [
                    f"WALLETS_CHECKED={checked_wallets}",
                    f"BAD_RESERVATIONS={bad_reservations}",
                    f"BAD_REQUEST_COSTS={bad_costs}",
                    f"FAILURES={failures}",
                ]
            )
        )
        if failures:
            self.stderr.write(self.style.ERROR("BILLING_INTEGRITY_FAILED"))
        else:
            self.stdout.write(self.style.SUCCESS("BILLING_INTEGRITY_OK"))
