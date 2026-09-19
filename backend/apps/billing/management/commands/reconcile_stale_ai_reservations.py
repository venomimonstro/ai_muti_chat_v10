from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.billing.models import BalanceReservation, RequestCost
from apps.billing.services import release
from apps.chat.models import Generation, Message
from apps.procurement.models import ProviderSpendReservation
from apps.procurement.services import release_provider_spend

ZERO = Decimal("0.0000")


class Command(BaseCommand):
    help = (
        "Recover stale customer AI reservations. Confirmed provider usage is charged "
        "at exact snapshotted retail cost; unconfirmed usage is released in full."
    )

    def add_arguments(self, parser):
        parser.add_argument("--older-than-minutes", type=int, default=30)
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        minutes = max(5, int(options["older_than_minutes"]))
        limit = max(1, min(int(options["limit"]), 5000))
        dry_run = bool(options["dry_run"])
        cutoff = timezone.now() - timedelta(minutes=minutes)
        queryset = (
            BalanceReservation.objects.filter(
                state=BalanceReservation.State.ACTIVE,
                created_at__lt=cutoff,
                idempotency_key__startswith="generation:",
            )
            .order_by("created_at")[:limit]
        )

        checked = recovered = charged = released = errors = 0
        for reservation in queryset:
            checked += 1
            try:
                generation_id = str(reservation.idempotency_key).split(":", 1)[1]
                generation = Generation.objects.filter(pk=generation_id).first()
                request_cost = RequestCost.objects.filter(generation_id=generation_id).first()
                confirmed_usage = bool(
                    request_cost is not None
                    and request_cost.provider_cost_rub is not None
                    and (request_cost.input_tokens or request_cost.output_tokens)
                )
                if dry_run:
                    self.stdout.write(
                        f"would_recover reservation={reservation.id} generation={generation_id} "
                        f"confirmed_provider_usage={confirmed_usage} amount={reservation.amount_rub}"
                    )
                    continue

                # release() is intentionally smart: with authoritative provider usage
                # it settles exact snapshotted retail cost; otherwise it refunds all.
                closed = release(reservation.id)
                actual = closed.actual_rub or ZERO
                if actual > ZERO:
                    charged += 1
                else:
                    released += 1

                if request_cost is not None:
                    prefix = f"chat:{request_cost.id}:"
                    if confirmed_usage:
                        # Re-fire the now-idempotent procurement signal in case the
                        # process died after persisting provider usage but before
                        # settling the internal provider funding reservation.
                        request_cost.save(update_fields=["reconciliation_status"])
                    else:
                        provider_reservations = ProviderSpendReservation.objects.filter(
                            source_key__startswith=prefix,
                            state=ProviderSpendReservation.State.ACTIVE,
                        )
                        for provider_reservation_id in provider_reservations.values_list(
                            "id", flat=True
                        ):
                            release_provider_spend(provider_reservation_id)

                if generation is not None:
                    if generation.state in {
                        Generation.State.QUEUED,
                        Generation.State.RUNNING,
                    }:
                        generation.state = Generation.State.FAILED
                        generation.error_code = "stale_recovery"
                        generation.actual_cost_rub = actual
                        generation.completed_at = timezone.now()
                        generation.save(
                            update_fields=[
                                "state",
                                "error_code",
                                "actual_cost_rub",
                                "completed_at",
                            ]
                        )
                        Message.objects.filter(
                            pk=generation.assistant_message_id,
                            status=Message.Status.STREAMING,
                        ).update(status=Message.Status.FAILED)
                recovered += 1
            except Exception as exc:
                errors += 1
                self.stderr.write(
                    f"recovery_failed reservation={reservation.id} error={type(exc).__name__}"
                )

        self.stdout.write(
            f"checked={checked} recovered={recovered} exact_charged={charged} "
            f"fully_released={released} errors={errors} dry_run={dry_run}"
        )
