import json

from django.core.management.base import BaseCommand

from apps.admin_ops.commercial_bootstrap import bootstrap_commercial_catalog


class Command(BaseCommand):
    help = "Bootstrap commercial providers, model placeholders and default billing/routing policies"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        result = bootstrap_commercial_catalog()
        if options["as_json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False))
            return
        self.stdout.write(self.style.SUCCESS("Commercial catalog bootstrap completed"))
        for key, value in result.items():
            self.stdout.write(f"{key}: {value}")
