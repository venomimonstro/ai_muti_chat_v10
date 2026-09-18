import json
import os
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, F
from django.utils import timezone

from apps.accounts.models import SupportRequest
from apps.b2b_api.models import APIUsage
from apps.billing.models import BalanceReservation, RequestCost
from apps.chat.models import CompareRun, Generation, Message
from apps.image_studio.models import ImageGeneration
from apps.payments.models import Payment

ZERO = Decimal("0")


class Command(BaseCommand):
    help = (
        "Fail-closed проверка пользовательского доверия: без списаний за ошибки, "
        "зависших резервов, невыданных платежей и заброшенной поддержки."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        blockers = []
        warnings = []

        failed_generation_ids = Generation.objects.filter(
            state=Generation.State.FAILED
        ).values("id")
        failed_chat_charges = RequestCost.objects.filter(
            generation_id__in=failed_generation_ids,
            charged_rub__gt=ZERO,
        ).count()
        failed_chat_actual = Generation.objects.filter(
            state=Generation.State.FAILED,
            actual_cost_rub__gt=ZERO,
        ).count()
        if failed_chat_charges or failed_chat_actual:
            blockers.append(
                f"failed_chat_was_charged={max(failed_chat_charges, failed_chat_actual)}"
            )

        invalid_cancelled_charges = 0
        for generation in (
            Generation.objects.filter(
                state=Generation.State.CANCELLED,
                actual_cost_rub__gt=ZERO,
            )
            .select_related("assistant_message")
            .iterator()
        ):
            request_cost = RequestCost.objects.filter(generation_id=generation.id).first()
            reservation = (
                BalanceReservation.objects.filter(pk=generation.reservation_id).first()
                if generation.reservation_id
                else None
            )
            charged = request_cost.charged_rub if request_cost else None
            valid_partial = all(
                [
                    generation.assistant_message.status == Message.Status.PARTIAL,
                    bool((generation.assistant_message.content or "").strip()),
                    charged is not None,
                    charged == generation.actual_cost_rub,
                    reservation is not None,
                    reservation.state == BalanceReservation.State.SETTLED,
                    reservation.actual_rub == generation.actual_cost_rub,
                    generation.actual_cost_rub <= reservation.amount_rub,
                ]
            )
            if not valid_partial:
                invalid_cancelled_charges += 1
        cancelled_without_output_charged = RequestCost.objects.filter(
            generation_id__in=Generation.objects.filter(
                state=Generation.State.CANCELLED,
                assistant_message__content="",
            ).values("id"),
            charged_rub__gt=ZERO,
        ).count()
        invalid_cancelled_charges += cancelled_without_output_charged
        if invalid_cancelled_charges:
            blockers.append(f"invalid_cancelled_chat_charge={invalid_cancelled_charges}")

        failed_images_charged = ImageGeneration.objects.filter(
            state=ImageGeneration.State.FAILED,
            actual_cost_rub__gt=ZERO,
        ).count()
        failed_images_reserved = ImageGeneration.objects.filter(
            state=ImageGeneration.State.FAILED,
            reservation__state=BalanceReservation.State.ACTIVE,
        ).count()
        completed_images = ImageGeneration.objects.filter(
            state=ImageGeneration.State.COMPLETED
        ).annotate(stored_images=Count("images"))
        completed_images_without_output = completed_images.filter(
            actual_count__lte=0
        ).count()
        completed_images_without_output += completed_images.filter(
            stored_images=0
        ).exclude(actual_count__lte=0).count()
        completed_images_count_mismatch = completed_images.exclude(
            stored_images=F("actual_count")
        ).count()
        completed_images_without_cost_record = completed_images.filter(
            actual_cost_rub__isnull=True
        ).count()
        if failed_images_charged:
            blockers.append(f"failed_images_were_charged={failed_images_charged}")
        if failed_images_reserved:
            blockers.append(f"failed_images_have_active_reservation={failed_images_reserved}")
        if completed_images_without_output:
            blockers.append(f"completed_images_without_output={completed_images_without_output}")
        if completed_images_count_mismatch:
            blockers.append(f"completed_image_asset_count_mismatch={completed_images_count_mismatch}")
        if completed_images_without_cost_record:
            blockers.append(
                f"completed_images_without_cost_record={completed_images_without_cost_record}"
            )

        failed_compare_charged = CompareRun.objects.filter(
            state=CompareRun.State.FAILED,
            actual_cost_rub__gt=ZERO,
        ).count()
        failed_compare_reserved = CompareRun.objects.filter(
            state=CompareRun.State.FAILED,
            reservation_id__in=BalanceReservation.objects.filter(
                state=BalanceReservation.State.ACTIVE
            ).values("id"),
        ).count()
        if failed_compare_charged:
            blockers.append(f"failed_compare_was_charged={failed_compare_charged}")
        if failed_compare_reserved:
            blockers.append(f"failed_compare_has_active_reservation={failed_compare_reserved}")

        failed_b2b_charged = APIUsage.objects.filter(
            state=APIUsage.State.FAILED,
            charged_rub__gt=ZERO,
        ).count()
        failed_b2b_reserved = APIUsage.objects.filter(
            state=APIUsage.State.FAILED,
            reservation__state=BalanceReservation.State.ACTIVE,
        ).count()
        if failed_b2b_charged:
            blockers.append(f"failed_b2b_was_charged={failed_b2b_charged}")
        if failed_b2b_reserved:
            blockers.append(f"failed_b2b_has_active_reservation={failed_b2b_reserved}")

        successful_not_credited = Payment.objects.filter(
            status=Payment.Status.SUCCEEDED,
            credited_at__isnull=True,
        ).count()
        if successful_not_credited:
            blockers.append(f"successful_payments_not_credited={successful_not_credited}")

        stale_hours = max(1, int(os.getenv("SUPPORT_MAX_UNANSWERED_HOURS", "48")))
        stale_before = timezone.now() - timedelta(hours=stale_hours)
        unanswered_support = SupportRequest.objects.filter(
            status__in=[SupportRequest.Status.OPEN, SupportRequest.Status.IN_PROGRESS],
            admin_reply="",
            created_at__lt=stale_before,
        ).count()
        if unanswered_support:
            blockers.append(f"support_unanswered_over_{stale_hours}h={unanswered_support}")
        near_sla_before = timezone.now() - timedelta(hours=max(1, stale_hours // 2))
        near_sla = SupportRequest.objects.filter(
            status__in=[SupportRequest.Status.OPEN, SupportRequest.Status.IN_PROGRESS],
            admin_reply="",
            created_at__lt=near_sla_before,
            created_at__gte=stale_before,
        ).count()
        if near_sla:
            warnings.append(f"support_approaching_sla={near_sla}")

        referenced_reservations = set(
            str(value)
            for value in Generation.objects.exclude(reservation_id__isnull=True).values_list(
                "reservation_id", flat=True
            )
        )
        referenced_reservations.update(
            str(value)
            for value in ImageGeneration.objects.exclude(reservation_id__isnull=True).values_list(
                "reservation_id", flat=True
            )
        )
        referenced_reservations.update(
            str(value)
            for value in APIUsage.objects.exclude(reservation_id__isnull=True).values_list(
                "reservation_id", flat=True
            )
        )
        referenced_reservations.update(
            str(value)
            for value in CompareRun.objects.exclude(reservation_id__isnull=True).values_list(
                "reservation_id", flat=True
            )
        )
        referenced_reservations.update(
            str(value)
            for value in CompareRun.objects.exclude(
                synthesis_reservation_id__isnull=True
            ).values_list("synthesis_reservation_id", flat=True)
        )
        orphan_active_reservations = BalanceReservation.objects.filter(
            state=BalanceReservation.State.ACTIVE,
        ).exclude(id__in=referenced_reservations).count()
        if orphan_active_reservations:
            blockers.append(f"orphan_active_reservations={orphan_active_reservations}")

        payload = {
            "ok": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "support_sla_hours": stale_hours,
            "checked_at": timezone.now().isoformat(),
            "recurring_payments_supported": False,
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False))
        else:
            for item in warnings:
                self.stdout.write(self.style.WARNING(f"WARN: {item}"))
            for item in blockers:
                self.stdout.write(self.style.ERROR(f"BLOCK: {item}"))
        if blockers:
            raise CommandError("Customer trust check failed: " + "; ".join(blockers))
        if not options["as_json"]:
            self.stdout.write(self.style.SUCCESS("CUSTOMER TRUST: PASS"))
