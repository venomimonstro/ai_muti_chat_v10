import json

from django.core.management.base import BaseCommand

from apps.admin_ops.commercial_bootstrap import bootstrap_commercial_catalog
from apps.admin_ops.compliance_manifest import bootstrap_compliance_manifest


class Command(BaseCommand):
    help = "Bootstrap commercial providers, models, policies and launch compliance manifest"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        result = bootstrap_commercial_catalog()
        result["compliance_items"] = bootstrap_compliance_manifest()
        if options["as_json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False))
            return
        self.stdout.write(self.style.SUCCESS("Commercial bootstrap completed"))
        for key, value in result.items():
            self.stdout.write(f"{key}: {value}")
