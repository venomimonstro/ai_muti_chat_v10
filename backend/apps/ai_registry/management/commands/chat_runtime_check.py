from django.core.management.base import BaseCommand

from apps.ai_registry.models import AIModel, ProviderApiKey
from apps.ai_registry.reliability import ensure_safe_client_models, provider_available
from apps.billing.pricing import active_price, quote
from apps.procurement.official_pricing import sync_official_prices


class Command(BaseCommand):
    help = (
        "Show why client chat models are or are not routable; --repair also restores "
        "verified official prices for providers with healthy keys and activates safe models."
    )

    def add_arguments(self, parser):
        parser.add_argument("--repair", action="store_true")

    def handle(self, *args, **options):
        if options["repair"]:
            provider_slugs = list(
                ProviderApiKey.objects.filter(
                    enabled=True,
                    health_state=ProviderApiKey.HealthState.HEALTHY,
                    provider__models__upstream_model__gt="",
                )
                .values_list("provider__slug", flat=True)
                .distinct()
            )
            for provider_slug in provider_slugs:
                try:
                    result = sync_official_prices(provider_slug=provider_slug)
                    self.stdout.write(
                        "official_prices "
                        f"provider={provider_slug} "
                        f"verified={len(result.get('verified', []))} "
                        f"rejected={len(result.get('rejected', []))} "
                        f"unsupported={len(result.get('unsupported', []))} "
                        f"usd_rub={result.get('usd_rub') or '-'}"
                    )
                    for item in result.get("rejected", []):
                        self.stdout.write(
                            self.style.WARNING(
                                f"price_rejected model={item.get('model')} "
                                f"upstream={item.get('upstream_model')} reason={item.get('detail')}"
                            )
                        )
                    for item in result.get("unsupported", []):
                        self.stdout.write(
                            self.style.WARNING(
                                f"price_unsupported model={item.get('model')} "
                                f"upstream={item.get('upstream_model')}"
                            )
                        )
                except Exception as exc:
                    # Diagnostics must continue even if an upstream pricing page or
                    # FX source is temporarily unavailable. We fail closed: no model
                    # is enabled without an active verified PriceVersion.
                    self.stdout.write(
                        self.style.ERROR(
                            f"official_prices provider={provider_slug} failed={exc}"
                        )
                    )

            repaired = ensure_safe_client_models()
            self.stdout.write(f"safe_models_repaired={repaired}")

        models = list(
            AIModel.objects.select_related("provider", "current_version").order_by(
                "provider__slug", "slug"
            )
        )
        if not models:
            self.stdout.write("NO_MODELS_CONFIGURED")
            return

        routable = 0
        for model in models:
            provider = model.provider
            healthy_keys = ProviderApiKey.objects.filter(
                provider=provider,
                enabled=True,
                health_state=ProviderApiKey.HealthState.HEALTHY,
            ).count()
            reasons = []
            if not model.enabled:
                reasons.append("model_disabled")
            if model.current_version_id is None:
                reasons.append("no_active_model_version")
            if healthy_keys == 0:
                reasons.append("no_healthy_api_key")
            if not provider_available(provider):
                reasons.append("provider_unavailable")

            input_margin = None
            output_margin = None
            try:
                price = active_price(model.slug)
                input_quote = quote(
                    price,
                    1_000_000,
                    0,
                    provider_slug=provider.slug,
                    model_slug=model.slug,
                )
                output_quote = quote(
                    price,
                    0,
                    1_000_000,
                    provider_slug=provider.slug,
                    model_slug=model.slug,
                )
                input_margin = input_quote.gross_margin_percent
                output_margin = output_quote.gross_margin_percent
                if not input_quote.margin_allowed or not output_quote.margin_allowed:
                    reasons.append("margin_below_floor")
            except Exception as exc:
                reasons.append(f"price_error:{exc}")

            if not reasons:
                routable += 1
            self.stdout.write(
                " | ".join(
                    [
                        f"model={model.slug}",
                        f"upstream={model.upstream_model}",
                        f"enabled={model.enabled}",
                        f"provider={provider.slug}",
                        f"provider_enabled={provider.enabled}",
                        f"health={provider.health_state}",
                        f"healthy_keys={healthy_keys}",
                        f"input_margin={input_margin}",
                        f"output_margin={output_margin}",
                        f"status={'ROUTABLE' if not reasons else 'BLOCKED'}",
                        f"reasons={','.join(reasons) if reasons else '-'}",
                    ]
                )
            )

        self.stdout.write(f"ROUTABLE_MODELS={routable}")
        if routable == 0:
            self.stderr.write("CHAT_BLOCKED: no routable models")
