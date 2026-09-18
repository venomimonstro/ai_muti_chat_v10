import json
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.ai_registry.models import Provider
from apps.b2b_api.models import APIKey, APIUsage, Organization
from apps.chat.models import CompareVariant
from apps.image_studio.models import ImageGeneration

from ...models import BalanceReservation, CostAnomaly, RequestCost, Wallet

ZERO = Decimal("0")


class Command(BaseCommand):
    help = "Fail-closed audit for conditions that can create unbounded provider spend or customer credit."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--stale-minutes", type=int, default=30)

    def handle(self, *args, **options):
        blockers = []
        warnings = []
        stale_before = timezone.now() - timedelta(minutes=max(5, options["stale_minutes"]))

        bad_wallets = Wallet.objects.filter(
            Q(available_rub__lt=0)
            | Q(reserved_rub__lt=0)
            | Q(paid_rub__lt=0)
            | Q(promo_rub__lt=0)
        ).count()
        bucket_mismatch = Wallet.objects.exclude(available_rub=F("paid_rub") + F("promo_rub")).count()
        if bad_wallets:
            blockers.append(f"negative_wallets={bad_wallets}")
        if bucket_mismatch:
            blockers.append(f"wallet_bucket_mismatch={bucket_mismatch}")

        active_reservations = BalanceReservation.objects.filter(state=BalanceReservation.State.ACTIVE)
        overdrawn_reservations = active_reservations.filter(amount_rub__lte=0).count()
        stale_reservations = active_reservations.filter(created_at__lt=stale_before).count()
        invalid_settlements = BalanceReservation.objects.filter(
            state=BalanceReservation.State.SETTLED
        ).filter(Q(actual_rub__lt=0) | Q(actual_rub__gt=F("amount_rub"))).count()
        if overdrawn_reservations:
            blockers.append(f"invalid_active_reservations={overdrawn_reservations}")
        if invalid_settlements:
            blockers.append(f"settlement_over_reservation={invalid_settlements}")
        if stale_reservations:
            warnings.append(f"stale_active_reservations={stale_reservations}")

        reserved_by_wallet = {
            row["wallet_id"]: row["total"] or ZERO
            for row in active_reservations.values("wallet_id").annotate(total=Sum("amount_rub"))
        }
        reservation_cache_mismatch = 0
        for wallet in Wallet.objects.only("id", "reserved_rub").iterator():
            if wallet.reserved_rub != reserved_by_wallet.get(wallet.id, ZERO):
                reservation_cache_mismatch += 1
        if reservation_cache_mismatch:
            blockers.append(f"wallet_reservation_mismatch={reservation_cache_mismatch}")

        loss_requests = RequestCost.objects.filter(
            provider_cost_rub__isnull=False,
            charged_rub__isnull=False,
            provider_cost_rub__gt=F("charged_rub"),
        ).count()
        charge_over_reserved_estimate = RequestCost.objects.filter(
            charged_rub__isnull=False,
            charged_rub__gt=F("estimated_rub"),
        ).count()
        if loss_requests:
            warnings.append(f"historical_provider_cost_above_customer_charge={loss_requests}")
        if charge_over_reserved_estimate:
            blockers.append(f"customer_charge_above_preflight_reserve={charge_over_reserved_estimate}")

        b2b_losses = APIUsage.objects.filter(
            state=APIUsage.State.COMPLETED,
            provider_cost_rub__gt=F("charged_rub"),
        )
        compare_losses = CompareVariant.objects.filter(
            state=CompareVariant.State.COMPLETED,
            provider_cost_rub__gt=F("actual_cost_rub"),
        )
        image_losses = ImageGeneration.objects.filter(
            state=ImageGeneration.State.COMPLETED,
            provider_cost_rub__isnull=False,
            actual_cost_rub__isnull=False,
            provider_cost_rub__gt=F("actual_cost_rub"),
        )
        if b2b_losses.exists():
            warnings.append(f"historical_b2b_negative_margin={b2b_losses.count()}")
        if compare_losses.exists():
            warnings.append(f"historical_compare_negative_margin={compare_losses.count()}")
        if image_losses.exists():
            warnings.append(f"historical_image_negative_margin={image_losses.count()}")

        loss_provider_ids = set(
            b2b_losses.values_list("model__provider_id", flat=True)
        )
        loss_provider_ids.update(
            compare_losses.values_list("model__provider_id", flat=True)
        )
        loss_provider_ids.update(
            image_losses.values_list("model__provider_id", flat=True)
        )
        unsafe_loss_providers = Provider.objects.filter(
            id__in=loss_provider_ids,
            enabled=True,
            emergency_disabled=False,
        ).count()
        if unsafe_loss_providers:
            blockers.append(f"loss_provider_still_enabled={unsafe_loss_providers}")

        open_critical = CostAnomaly.objects.filter(
            status=CostAnomaly.Status.OPEN,
            severity="critical",
        ).count()
        if open_critical:
            blockers.append(f"unresolved_critical_cost_anomalies={open_critical}")

        exposed_loss_providers = set(
            CostAnomaly.objects.filter(status=CostAnomaly.Status.OPEN, severity="critical")
            .exclude(provider_slug="")
            .values_list("provider_slug", flat=True)
        )
        unsafe_enabled = Provider.objects.filter(
            slug__in=exposed_loss_providers,
            enabled=True,
            emergency_disabled=False,
        ).count()
        if unsafe_enabled:
            blockers.append(f"critical_cost_anomaly_provider_still_enabled={unsafe_enabled}")

        running_b2b = APIUsage.objects.filter(
            state=APIUsage.State.RUNNING,
            created_at__lt=stale_before,
        ).count()
        active_org_without_limit = Organization.objects.filter(
            active=True,
            monthly_limit_rub__isnull=True,
        ).count()
        active_keys_without_limit = APIKey.objects.filter(
            revoked_at__isnull=True,
            organization__active=True,
            monthly_limit_rub__isnull=True,
            organization__monthly_limit_rub__isnull=True,
        ).count()
        if running_b2b:
            warnings.append(f"stale_running_b2b_requests={running_b2b}")
        if active_org_without_limit:
            warnings.append(f"b2b_orgs_without_monthly_limit={active_org_without_limit}")
        if active_keys_without_limit:
            blockers.append(f"b2b_keys_without_any_spend_limit={active_keys_without_limit}")

        zombie_images = ImageGeneration.objects.filter(
            state=ImageGeneration.State.RUNNING,
            created_at__lt=stale_before,
        ).count()
        failed_with_active_reservation = ImageGeneration.objects.filter(
            state=ImageGeneration.State.FAILED,
            reservation__state=BalanceReservation.State.ACTIVE,
        ).count()
        if zombie_images:
            warnings.append(f"stale_running_image_generations={zombie_images}")
        if failed_with_active_reservation:
            blockers.append(f"failed_images_with_active_reservation={failed_with_active_reservation}")

        payload = {
            "ok": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "checked_at": timezone.now().isoformat(),
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False))
        else:
            for item in warnings:
                self.stdout.write(self.style.WARNING(f"WARN: {item}"))
            for item in blockers:
                self.stdout.write(self.style.ERROR(f"BLOCK: {item}"))
        if blockers:
            raise CommandError("Economic safety check failed: " + "; ".join(blockers))
        if not options["as_json"]:
            self.stdout.write(self.style.SUCCESS("ECONOMIC SAFETY: PASS"))
