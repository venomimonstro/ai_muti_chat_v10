from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.reliability import ensure_safe_client_models
from apps.procurement.official_pricing import sync_official_prices


class Command(BaseCommand):
    help = (
        "Verify official provider pricing, create active PriceVersion rows for "
        "already discovered AI models, and optionally activate commercially safe models."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            default="",
            help="Provider slug to sync (for example: openai). Empty means all providers.",
        )
        parser.add_argument(
            "--activate-safe",
            action="store_true",
            help="After pricing sync, enable saved models that have a healthy key, active version and safe margin.",
        )

    def handle(self, *args, **options):
        provider = str(options.get("provider") or "").strip()
        try:
            result = sync_official_prices(provider_slug=provider)
        except Exception as exc:
            raise CommandError(f"Official pricing sync failed: {exc}") from exc

        verified = result.get("verified", [])
        rejected = result.get("rejected", [])
        unsupported = result.get("unsupported", [])
        expired = result.get("expired", [])

        self.stdout.write(
            f"USD_RUB={result.get('usd_rub') or '-'} source={result.get('fx_source') or '-'}"
        )
        for item in verified:
            self.stdout.write(
                self.style.SUCCESS(
                    "PRICE_OK "
                    f"model={item['model']} upstream={item['upstream_model']} "
                    f"input_usd={item['input_usd_per_million']} "
                    f"output_usd={item['output_usd_per_million']} "
                    f"price_version={item['price_version']}"
                )
            )
        for item in rejected:
            self.stdout.write(
                self.style.ERROR(
                    f"PRICE_REJECTED model={item['model']} upstream={item['upstream_model']} "
                    f"reason={item['detail']} source={item['source_url']}"
                )
            )
        for item in unsupported:
            self.stdout.write(
                self.style.WARNING(
                    f"PRICE_UNSUPPORTED model={item['model']} upstream={item['upstream_model']} "
                    f"provider={item['provider']}"
                )
            )
        for item in expired:
            self.stdout.write(
                self.style.WARNING(
                    f"PRICE_EXPIRED model={item['model']} upstream={item['upstream_model']} "
                    f"effective_until={item['effective_until']}"
                )
            )

        activated = 0
        if options.get("activate_safe"):
            activated = ensure_safe_client_models()
            self.stdout.write(self.style.SUCCESS(f"SAFE_MODELS_ACTIVATED={activated}"))

        self.stdout.write(
            f"VERIFIED={len(verified)} REJECTED={len(rejected)} "
            f"UNSUPPORTED={len(unsupported)} EXPIRED={len(expired)}"
        )
        if not verified:
            raise CommandError(
                "No official prices were applied. Check PRICE_REJECTED/PRICE_UNSUPPORTED lines above."
            )
