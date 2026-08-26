import json
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django.utils import timezone

from apps.admin_ops.models import BackupRecord, ComplianceSignoff, ReleaseRecord


class Command(BaseCommand):
    help = "Validate technical and evidence-based launch gates"

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        results = self._structural_checks()
        if options["strict"]:
            results.extend(self._production_checks())
            results.extend(self._evidence_checks())
        failed = [item for item in results if not item["passed"]]
        if options["as_json"]:
            self.stdout.write(json.dumps({"checks": results, "passed": not failed}, ensure_ascii=False))
        else:
            for item in results:
                marker = "PASS" if item["passed"] else "BLOCK"
                self.stdout.write(f"[{marker}] {item['name']}: {item['detail']}")
        if failed:
            raise CommandError(f"Pre-launch blocked by {len(failed)} check(s)")
        self.stdout.write(self.style.SUCCESS("Pre-launch checks passed"))

    def _check(self, name, passed, detail):
        return {"name": name, "passed": bool(passed), "detail": detail}

    def _structural_checks(self):
        status_path = reverse("public-status")
        return [
            self._check("status_page", status_path == "/api/v1/status/", status_path),
            self._check(
                "upload_limit",
                settings.FILE_MAX_UPLOAD_BYTES <= settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
                f"file={settings.FILE_MAX_UPLOAD_BYTES}, request={settings.DATA_UPLOAD_MAX_MEMORY_SIZE}",
            ),
            self._check(
                "cost_limits",
                settings.B2B_API_MAX_OUTPUT_TOKENS > 0
                and settings.COMPARE_MAX_OUTPUT_TOKENS > 0,
                "B2B and Compare output caps are configured",
            ),
            self._check(
                "security_middleware",
                "config.middleware.SecurityHeadersMiddleware" in settings.MIDDLEWARE,
                "CSP and browser hardening middleware",
            ),
        ]

    def _production_checks(self):
        return [
            self._check("debug_disabled", not settings.DEBUG, "DJANGO_DEBUG=false"),
            self._check(
                "secret_key",
                len(settings.SECRET_KEY) >= 32 and "unsafe" not in settings.SECRET_KEY,
                "Dedicated secret with at least 32 characters",
            ),
            self._check(
                "api_key_pepper",
                settings.B2B_API_KEY_PEPPER != settings.SECRET_KEY,
                "B2B API pepper is independent from Django secret",
            ),
            self._check(
                "secure_cookies",
                settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE,
                "Secure session and CSRF cookies",
            ),
            self._check(
                "https",
                settings.SECURE_SSL_REDIRECT and settings.SECURE_HSTS_SECONDS >= 86400,
                "HTTPS redirect and HSTS enabled",
            ),
            self._check(
                "admin_mfa",
                settings.ADMIN_MFA_ENFORCED,
                "MFA must be enforced by the selected identity layer",
            ),
            self._check(
                "payments_fiscalization",
                not settings.PAYMENTS_LIVE_ENABLED
                or settings.PAYMENTS_FISCALIZATION_MODE != "disabled",
                "Live payments require a reviewed fiscalization mode",
            ),
        ]

    def _evidence_checks(self):
        required = set(ComplianceSignoffViewKeys.values)
        approved = set(
            ComplianceSignoff.objects.filter(
                status=ComplianceSignoff.Status.APPROVED
            ).values_list("key", flat=True)
        )
        backup = BackupRecord.objects.filter(
            status=BackupRecord.Status.RESTORED,
            restored_at__gte=timezone.now() - timedelta(days=30),
        ).exists()
        rollback = ReleaseRecord.objects.filter(
            state=ReleaseRecord.State.ROLLED_BACK
        ).exists()
        return [
            self._check(
                "compliance_signoffs",
                required <= approved,
                f"approved {len(required & approved)}/{len(required)}",
            ),
            self._check("restore_drill", backup, "Successful restore drill within 30 days"),
            self._check("rollback_drill", rollback, "At least one recorded rollback drill"),
        ]


class ComplianceSignoffViewKeys:
    values = (
        "entity-tax-regime",
        "wallet-fiscalization",
        "receipt-refund-flow",
        "privacy-data-flow",
        "provider-commercial-terms",
        "public-legal-documents",
        "admin-mfa",
    )
