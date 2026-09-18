import json
import os
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.payments.models import PaymentFeeVersion, ReconciliationRun


class Command(BaseCommand):
    help = "Validate that live payments are configured for commercial traffic"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--require-reconciliation", action="store_true")

    def handle(self, *args, **options):
        checks = []

        def add(name, passed, detail):
            checks.append({"name": name, "passed": bool(passed), "detail": detail})

        add("payments_enabled", settings.PAYMENTS_ENABLED, "PAYMENTS_ENABLED=true")
        add("live_enabled", settings.PAYMENTS_LIVE_ENABLED, "PAYMENTS_LIVE_ENABLED=true")
        add("shop_id", bool(settings.YOOKASSA_SHOP_ID), "YOOKASSA_SHOP_ID configured")
        add("secret_key", bool(settings.YOOKASSA_SECRET_KEY), "YOOKASSA_SECRET_KEY configured")
        add(
            "https_return_url",
            str(settings.PAYMENT_RETURN_URL).startswith("https://"),
            str(settings.PAYMENT_RETURN_URL),
        )
        add(
            "fiscalization",
            settings.PAYMENTS_FISCALIZATION_MODE == "provider_receipt",
            settings.PAYMENTS_FISCALIZATION_MODE,
        )
        add("vat_code", 1 <= int(settings.PAYMENTS_VAT_CODE) <= 6, str(settings.PAYMENTS_VAT_CODE))
        try:
            minimum = Decimal(str(settings.PAYMENT_MIN_RUB))
            maximum = Decimal(str(settings.PAYMENT_MAX_RUB))
            limits_valid = minimum > 0 and maximum >= minimum
        except (InvalidOperation, TypeError, ValueError):
            minimum = settings.PAYMENT_MIN_RUB
            maximum = settings.PAYMENT_MAX_RUB
            limits_valid = False
        add("payment_limits", limits_valid, f"{minimum}..{maximum}")

        fee = (
            PaymentFeeVersion.objects.filter(
                provider="yookassa", active=True, effective_from__lte=timezone.now()
            )
            .order_by("-effective_from")
            .first()
        )
        add(
            "acquiring_fee",
            fee is not None,
            "active YooKassa PaymentFeeVersion" if fee else "missing active fee version",
        )
        if options["require_reconciliation"]:
            max_age = max(
                int(os.getenv("PAYMENT_RECONCILIATION_MAX_AGE_SECONDS", "86400")), 300
            )
            cutoff = timezone.now() - timedelta(seconds=max_age)
            reconciliation = ReconciliationRun.objects.order_by("-started_at").first()
            reconciliation_ready = bool(
                reconciliation
                and reconciliation.status == ReconciliationRun.Status.SUCCEEDED
                and reconciliation.finished_at
                and reconciliation.finished_at >= cutoff
            )
            detail = (
                f"status={reconciliation.status}, finished_at={reconciliation.finished_at}, max_age={max_age}s"
                if reconciliation
                else "never run"
            )
            add("reconciliation", reconciliation_ready, detail)

        failed = [item for item in checks if not item["passed"]]
        payload = {"checks": checks, "passed": not failed}
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
            if failed:
                raise CommandError(f"Live payments blocked by {len(failed)} check(s)")
            return

        for item in checks:
            self.stdout.write(f"[{'PASS' if item['passed'] else 'BLOCK'}] {item['name']}: {item['detail']}")
        if failed:
            raise CommandError(f"Live payments blocked by {len(failed)} check(s)")
        self.stdout.write(self.style.SUCCESS("Live payment checks passed"))
