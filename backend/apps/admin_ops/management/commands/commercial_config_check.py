import json

from django.core.management.base import BaseCommand, CommandError

from apps.admin_ops.commercial_bootstrap import commercial_setup_status


class Command(BaseCommand):
    help = "Validate that the commercial AI catalog is configured safely enough to serve paid traffic"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--require-healthy",
            action="store_true",
            help="Also require every enabled provider to have a healthy last check",
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

        enabled_models = 0
        for provider in status["providers"]:
            provider_enabled = provider["enabled"]
            if provider_enabled:
                add(
                    f"provider:{provider['slug']}:credential",
                    provider["credential_configured"],
                    provider["credential_env"],
                )
                if options["require_healthy"]:
                    add(
                        f"provider:{provider['slug']}:health",
                        provider["health_state"] == "healthy",
                        provider["health_state"],
                    )
            for model in provider["models"]:
                if not model["enabled"]:
                    continue
                enabled_models += 1
                add(
                    f"model:{model['slug']}:provider_enabled",
                    provider_enabled,
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
                add(
                    f"model:{model['slug']}:price",
                    model["has_active_price"],
                    "active price required",
                )

        add("enabled_model", enabled_models > 0, f"enabled models: {enabled_models}")
        failed = [item for item in checks if not item["passed"]]

        if options["as_json"]:
            self.stdout.write(
                json.dumps({"checks": checks, "passed": not failed}, ensure_ascii=False)
            )
        else:
            for item in checks:
                marker = "PASS" if item["passed"] else "BLOCK"
                self.stdout.write(f"[{marker}] {item['name']}: {item['detail']}")

        if failed:
            raise CommandError(f"Commercial configuration blocked by {len(failed)} check(s)")
        self.stdout.write(self.style.SUCCESS("Commercial configuration checks passed"))
