from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.billing.models import FxRateSnapshot


class Command(BaseCommand):
    help = "Record an immutable provider-currency to RUB FX snapshot"

    def add_arguments(self, parser):
        parser.add_argument("--base", required=True)
        parser.add_argument("--rate", required=True)
        parser.add_argument("--source", required=True)
        parser.add_argument("--reference", default="")

    def handle(self, *args, **options):
        try:
            rate = Decimal(options["rate"])
        except InvalidOperation as exc:
            raise CommandError("Invalid decimal rate") from exc
        if rate <= 0:
            raise CommandError("FX rate must be positive")
        snapshot = FxRateSnapshot.objects.create(
            base_currency=options["base"],
            quote_currency="RUB",
            rate=rate,
            source=options["source"],
            source_reference=options["reference"],
            effective_at=timezone.now(),
        )
        self.stdout.write(f"fx={snapshot.base_currency}/RUB rate={snapshot.rate} id={snapshot.id}")
