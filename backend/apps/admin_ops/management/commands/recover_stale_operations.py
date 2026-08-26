import json

from django.core.management.base import BaseCommand

from apps.admin_ops.recovery import recover_stale_operations


class Command(BaseCommand):
    help = "Release reservations and close operations abandoned by interrupted workers"

    def handle(self, *args, **options):
        result = recover_stale_operations()
        self.stdout.write(json.dumps(result, sort_keys=True))
