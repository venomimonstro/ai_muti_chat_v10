from django.core.management.base import BaseCommand

from apps.ai_registry.models import Provider
from apps.ai_registry.reliability import check_provider


class Command(BaseCommand):
    help = "Checks configured AI providers and updates circuit health state."

    def handle(self, *_args, **_options):
        failed = 0
        for provider in Provider.objects.all():
            health = check_provider(provider)
            provider.refresh_from_db()
            if health is None or not health.healthy:
                failed += 1
            self.stdout.write(f"{provider.slug}: {provider.health_state}")
        if failed:
            self.stderr.write(self.style.WARNING(f"Unhealthy providers: {failed}"))
