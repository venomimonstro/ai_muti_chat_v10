from django.core.management.base import BaseCommand

from apps.admin_ops.compliance_manifest import bootstrap_compliance_manifest


class Command(BaseCommand):
    help = "Create the commercial launch compliance signoff checklist"

    def handle(self, *args, **options):
        created = bootstrap_compliance_manifest()
        self.stdout.write(self.style.SUCCESS(f"Compliance manifest ready; created {created} item(s)"))
