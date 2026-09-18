from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.chat.models import CompareRun, Generation, Message
from apps.image_studio.models import ImageGeneration

from ...models import BalanceReservation
from ...services import release


class Command(BaseCommand):
    help = "Release abandoned reservations only after the owning operation is moved to a terminal safe state."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-minutes", type=int, default=30)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(minutes=max(5, options["older_than_minutes"]))
        dry_run = options["dry_run"]
        recovered = 0
        skipped = 0

        reservations = BalanceReservation.objects.filter(
            state=BalanceReservation.State.ACTIVE,
            created_at__lt=cutoff,
        ).order_by("created_at")

        for reservation in reservations.iterator():
            key = reservation.idempotency_key
            action = self._classify_and_recover(reservation, key, dry_run=dry_run)
            if action == "recovered":
                recovered += 1
            else:
                skipped += 1

        mode = "DRY RUN" if dry_run else "DONE"
        self.stdout.write(
            self.style.SUCCESS(
                f"STALE RESERVATION RECOVERY {mode}: recovered={recovered} skipped={skipped}"
            )
        )

    def _classify_and_recover(self, reservation, key, *, dry_run):
        if key.startswith("generation:"):
            return self._recover_chat(reservation, key.split(":", 1)[1], dry_run=dry_run)
        if key.startswith("image:"):
            return self._recover_image(reservation, key.split(":", 1)[1], dry_run=dry_run)
        if key.startswith("compare-synthesis:"):
            return self._recover_compare_synthesis(
                reservation, key.split(":", 1)[1], dry_run=dry_run
            )
        if key.startswith("compare:"):
            return self._recover_compare(reservation, key.split(":", 1)[1], dry_run=dry_run)
        self.stdout.write(self.style.WARNING(f"SKIP unknown reservation key={key}"))
        return "skipped"

    def _recover_chat(self, reservation, generation_id, *, dry_run):
        generation = Generation.objects.filter(pk=generation_id).select_related(
            "assistant_message"
        ).first()
        if generation is None or generation.reservation_id != reservation.id:
            self.stdout.write(self.style.WARNING(f"SKIP orphan/mismatch chat reservation={reservation.id}"))
            return "skipped"
        if generation.state == Generation.State.COMPLETED:
            self.stdout.write(self.style.WARNING(f"SKIP completed chat reservation={reservation.id}"))
            return "skipped"
        if dry_run:
            return "recovered"
        with transaction.atomic():
            locked = Generation.objects.select_for_update().select_related("assistant_message").get(
                pk=generation.pk
            )
            if locked.state == Generation.State.COMPLETED:
                return "skipped"
            release(reservation.id)
            assistant = locked.assistant_message
            assistant.status = Message.Status.PARTIAL if assistant.content else Message.Status.FAILED
            assistant.save(update_fields=["status"])
            locked.state = Generation.State.FAILED
            locked.error_code = "stale_reservation_recovered"
            locked.completed_at = timezone.now()
            locked.save(update_fields=["state", "error_code", "completed_at"])
        return "recovered"

    def _recover_image(self, reservation, generation_id, *, dry_run):
        generation = ImageGeneration.objects.filter(pk=generation_id).first()
        if generation is None or generation.reservation_id != reservation.id:
            self.stdout.write(self.style.WARNING(f"SKIP orphan/mismatch image reservation={reservation.id}"))
            return "skipped"
        if generation.state == ImageGeneration.State.COMPLETED:
            self.stdout.write(self.style.WARNING(f"SKIP completed image reservation={reservation.id}"))
            return "skipped"
        if dry_run:
            return "recovered"
        with transaction.atomic():
            locked = ImageGeneration.objects.select_for_update().get(pk=generation.pk)
            if locked.state == ImageGeneration.State.COMPLETED:
                return "skipped"
            release(reservation.id)
            for image in locked.images.all():
                image.file.delete(save=False)
            locked.images.all().delete()
            locked.state = ImageGeneration.State.FAILED
            locked.error_code = "stale_reservation_recovered"
            locked.completed_at = timezone.now()
            locked.save(update_fields=["state", "error_code", "completed_at"])
        return "recovered"

    def _recover_compare(self, reservation, run_id, *, dry_run):
        run = CompareRun.objects.filter(pk=run_id).first()
        if run is None or run.reservation_id != reservation.id:
            self.stdout.write(self.style.WARNING(f"SKIP orphan/mismatch compare reservation={reservation.id}"))
            return "skipped"
        if run.state == CompareRun.State.COMPLETED:
            self.stdout.write(self.style.WARNING(f"SKIP completed compare reservation={reservation.id}"))
            return "skipped"
        if dry_run:
            return "recovered"
        with transaction.atomic():
            locked = CompareRun.objects.select_for_update().get(pk=run.pk)
            if locked.state == CompareRun.State.COMPLETED:
                return "skipped"
            release(reservation.id)
            locked.state = (
                CompareRun.State.PARTIAL
                if locked.variants.filter(state="completed").exists()
                else CompareRun.State.FAILED
            )
            locked.completed_at = timezone.now()
            locked.save(update_fields=["state", "completed_at"])
        return "recovered"

    def _recover_compare_synthesis(self, reservation, run_id, *, dry_run):
        run = CompareRun.objects.filter(pk=run_id).first()
        if run is None or run.synthesis_reservation_id != reservation.id:
            self.stdout.write(self.style.WARNING(f"SKIP orphan/mismatch synthesis reservation={reservation.id}"))
            return "skipped"
        if run.synthesis_output:
            self.stdout.write(self.style.WARNING(f"SKIP completed synthesis reservation={reservation.id}"))
            return "skipped"
        if dry_run:
            return "recovered"
        with transaction.atomic():
            locked = CompareRun.objects.select_for_update().get(pk=run.pk)
            if locked.synthesis_output:
                return "skipped"
            release(reservation.id)
            locked.synthesis_reservation_id = None
            locked.save(update_fields=["synthesis_reservation_id"])
        return "recovered"
