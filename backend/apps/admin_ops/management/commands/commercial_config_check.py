import json
import os
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.admin_ops.commercial_bootstrap import commercial_setup_status
from apps.ai_registry.models import Provider
from apps.billing.models import PriceVersion


class Command(BaseCommand):
    help = "Validate that the commercial AI catalog is configured safely enough to serve paid traffic"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--require-healthy",
            action="store_true",
            help="Also require every enabled provider to have a recent healthy check",
        )

    def handle(self, *args, **options):
        status = commercial_setup_status()
        checks = []

        def add(name, passed, detail):
            checks.append({"name": name, "passed": bool(passed), "detail": detail})

        add("routing_policy", status["routing_policy"], "Active routing policy")
        add("markup_policy", status["markup_policy"], "Active global markup policy")
        add("margin_policy", status["margin_policy"], "Active margin guard policy")
        add("rub_fx_identity", status["rub_fx_identity"], "RUB/RUB identity FX snapshot")

        now = timezone.now()
        health_max_age = max(int(os.getenv("AI_PROVIDER_HEALTH_MAX_AGE_SECONDS", "900")), 60)
        health_cutoff = now - timedelta(seconds=health_max_age)
        provider_objects = {
            item.slug: item
            for item in Provider.objects.only(
                "slug", "health_state", "last_checked_at", "enabled", "emergency_disabled"
            )
        }

        enabled_models = 0
        for provider in status["providers"]:
            provider_enabled = provider["enabled"]
            record = provider_objects.get(provider["slug"])
            if provider_enabled:
                add(
                    f"provider:{provider['slug']}:credential",
                    provider["credential_configured"],
                    provider["credential_env"],
                )
                add(
                    f"provider:{provider['slug']}:emergency_state",
                    bool(record and not record.emergency_disabled),
                    "provider must not be emergency-disabled",
                )
                if options["require_healthy"]:
                    health_fresh = bool(
                        record
                        and record.health_state == Provider.HealthState.HEALTHY
                        and record.last_checked_at
                        and record.last_checked_at >= health_cutoff
                    )
                    checked_at = record.last_checked_at.isoformat() if record and record.last_checked_at else "never"
                    add(
                        f"provider:{provider['slug']}:health",
                        health_fresh,
                        f"state={provider['health_state']}, checked_at={checked_at}, max_age={health_max_age}s",
                    )
            for model in provider["models"]:
                if not model["enabled"]:
                    continue
                enabled_models += 1
                add(
                    f"model:{model['slug']}:provider_enabled",
                    provider_enabled and bool(record and not record.emergency_disabled),
                    provider["slug"],
                )
                add(
                    f"model:{model['slug']}:upstream",
                    bool(model["upstream_model"]),
                    model["upstream_model"] or "missing",
                )
                add(
                    f"model:{model['slug']}:version",
                    model["has_active_version"],
                    "active version required",
                )
                price = (
                    PriceVersion.objects.filter(
                        model_slug=model["slug"],
                        active=True,
                        effective_from__lte=now,
                    )
                    .order_by("-effective_from", "-created_at")
                    .first()
                )
                positive_price = bool(
                    price
                    and price.input_rub_per_million > Decimal("0")
                    and price.output_rub_per_million > Decimal("0")
                )
                add(
                    f"model:{model['slug']}:price",
                    positive_price,
                    "positive price effective now is required",
                )

        add("enabled_model", enabled_models > 0, f"enabled models: {enabled_models}")
        failed = [item for item in checks if not item["passed"]]

        if options["as_json"]:
            self.stdout.write(json.dumps({"checks": checks, "passed": not failed}, ensure_ascii=False))
            if failed:
                raise CommandError(f"Commercial configuration blocked by {len(failed)} check(s)")
            return

        for item in checks:
            marker = "PASS" if item["passed"] else "BLOCK"
            self.stdout.write(f"[{marker}] {item['name']}: {item['detail']}")
        if failed:
            raise CommandError(f"Commercial configuration blocked by {len(failed)} check(s)")
        self.stdout.write(self.style.SUCCESS("Commercial configuration checks passed"))
