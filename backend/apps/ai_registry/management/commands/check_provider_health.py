from django.core.management.base import BaseCommand

from apps.ai_registry.models import Provider
from apps.ai_registry.reliability import check_provider


class Command(BaseCommand):
    help = "Checks configured AI providers and updates circuit health state."

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            default="",
            help="Optional provider slug to check only one provider.",
        )
        parser.add_argument(
            "--live",
            action="store_true",
            help="Require a real minimal inference before declaring a provider healthy.",
        )

    def handle(self, *_args, **options):
        failed = 0
        disabled = 0
        providers = Provider.objects.all()
        provider_slug = str(options.get("provider") or "").strip()
        if provider_slug:
            providers = providers.filter(slug=provider_slug)
        for provider in providers:
            if options.get("live"):
                provider._force_inference_probe = True
            health = check_provider(provider)
            provider.refresh_from_db()
            if not provider.enabled or provider.emergency_disabled:
                disabled += 1
                self.stdout.write(f"{provider.slug}: {provider.health_state} (admin disabled)")
                continue
            if health is None or not health.healthy:
                failed += 1
            self.stdout.write(f"{provider.slug}: {provider.health_state}")
        if failed:
            self.stderr.write(self.style.WARNING(f"Unhealthy enabled providers: {failed}"))
        if disabled:
            self.stdout.write(f"Admin-disabled providers: {disabled}")
