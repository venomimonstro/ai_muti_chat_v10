import json
from decimal import Decimal
from pathlib import Path

import httpx
from django.core.management.base import BaseCommand, CommandError

from apps.billing.provider_pricing_sync import sync_pricing_catalog


class Command(BaseCommand):
    help = "Compare/apply versioned provider prices and FX from a reviewed JSON catalog"

    def add_arguments(self, parser):
        parser.add_argument("source", help="HTTPS URL or local JSON file")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--anomaly-threshold", type=Decimal, default=Decimal("10"))

    def handle(self, *args, **options):
        source = options["source"]
        try:
            if source.startswith("https://"):
                response = httpx.get(source, timeout=15, follow_redirects=False)
                response.raise_for_status()
                payload = response.json()
            elif source.startswith("http://"):
                raise CommandError("Pricing feed must use HTTPS")
            else:
                payload = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, ValueError, httpx.HTTPError) as exc:
            raise CommandError(f"Cannot load pricing catalog: {exc}") from exc
        try:
            result = sync_pricing_catalog(
                payload,
                apply=options["apply"],
                anomaly_threshold_percent=options["anomaly_threshold"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        for change in result["changes"]:
            marker = "APPLIED" if change.applied else "CHANGE" if change.changed else "OK"
            self.stdout.write(
                f"[{marker}] {change.model_slug}: input {change.input_change_percent:.2f}% / "
                f"output {change.output_change_percent:.2f}%"
            )
        self.stdout.write(f"FX snapshots created: {result['fx_created']}")
        if any(item.changed and not item.applied for item in result["changes"]):
            self.stdout.write(self.style.WARNING("Changes detected but not applied; rerun with --apply after review"))
