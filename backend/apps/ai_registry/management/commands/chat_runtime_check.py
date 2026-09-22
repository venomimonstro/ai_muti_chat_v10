from django.core.management.base import BaseCommand

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.models import AIModel, ProviderApiKey
from apps.ai_registry.reliability import ensure_safe_client_models, provider_available
from apps.billing.pricing import active_price, quote
from apps.procurement.official_pricing import sync_official_prices


class Command(BaseCommand):
    help = (
        "Show why client chat models are or are not routable; --repair restores verified "
        "official prices and safe models; --live performs a minimal real provider inference."
    )

    def add_arguments(self, parser):
        parser.add_argument("--repair", action="store_true")
        parser.add_argument("--live", action="store_true")
        parser.add_argument(
            "--model",
            default="",
            help="Optional internal model slug to inspect/test, e.g. openai-gpt-56-luna.",
        )

    def _live_check(self, model):
        provider = model.provider
        self.stdout.write(
            "LIVE_START "
            f"model={model.slug} upstream={model.upstream_model} provider={provider.slug}"
        )
        if provider.emergency_disabled:
            self.stderr.write(
                self.style.ERROR(
                    f"LIVE_BLOCKED model={model.slug} code=provider_emergency_disabled"
                )
            )
            return False
        if not provider_available(provider):
            self.stderr.write(
                self.style.ERROR(
                    f"LIVE_BLOCKED model={model.slug} code=provider_unavailable"
                )
            )
            return False
        try:
            adapter = adapter_for(model)
            result = adapter.generate(
                model=model.upstream_model,
                messages=[{"role": "user", "content": "Ответь только: OK"}],
                max_output_tokens=32,
            )
        except ProviderError as exc:
            self.stderr.write(
                self.style.ERROR(
                    "LIVE_FAILED "
                    f"model={model.slug} provider={provider.slug} "
                    f"code={exc.code} retryable={exc.retryable} detail={exc}"
                )
            )
            return False
        except Exception as exc:
            self.stderr.write(
                self.style.ERROR(
                    "LIVE_FAILED "
                    f"model={model.slug} provider={provider.slug} "
                    f"type={type(exc).__name__} detail={exc}"
                )
            )
            return False
        text = (result.text or "").replace("\n", " ")[:120]
        self.stdout.write(
            self.style.SUCCESS(
                "LIVE_OK "
                f"model={model.slug} provider={provider.slug} "
                f"request_id={result.provider_request_id or '-'} "
                f"input_tokens={result.input_tokens} output_tokens={result.output_tokens} "
                f"text={text!r}"
            )
        )
        return True

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
                    self.stdout.write(
                        self.style.ERROR(
                            f"official_prices provider={provider_slug} failed={exc}"
                        )
                    )

            repaired = ensure_safe_client_models()
            self.stdout.write(f"safe_models_repaired={repaired}")

        queryset = AIModel.objects.select_related("provider", "current_version").order_by(
            "provider__slug", "slug"
        )
        requested_model = str(options.get("model") or "").strip()
        if requested_model:
            queryset = queryset.filter(slug=requested_model)
        models = list(queryset)
        if not models:
            self.stdout.write("NO_MODELS_CONFIGURED")
            return

        routable = 0
        live_failures = 0
        for model in models:
            provider = model.provider
            keys = list(
                ProviderApiKey.objects.filter(provider=provider, enabled=True).order_by(
                    "priority", "created_at"
                )
            )
            healthy_keys = sum(
                1 for key in keys if key.health_state == ProviderApiKey.HealthState.HEALTHY
            )
            reasons = []
            if not model.enabled:
                reasons.append("model_disabled")
            if model.current_version_id is None:
                reasons.append("no_active_model_version")
            if healthy_keys == 0:
                reasons.append("no_healthy_api_key")
            if provider.emergency_disabled:
                reasons.append("provider_emergency_disabled")
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
                        f"emergency_disabled={provider.emergency_disabled}",
                        f"health={provider.health_state}",
                        f"consecutive_failures={provider.consecutive_failures}",
                        f"healthy_keys={healthy_keys}",
                        f"keys={';'.join(f'{k.label}:{k.health_state}' for k in keys) or '-'}",
                        f"input_margin={input_margin}",
                        f"output_margin={output_margin}",
                        f"status={'ROUTABLE' if not reasons else 'BLOCKED'}",
                        f"reasons={','.join(reasons) if reasons else '-'}",
                    ]
                )
            )
            if options["live"] and not self._live_check(model):
                live_failures += 1

        self.stdout.write(f"ROUTABLE_MODELS={routable}")
        if options["live"]:
            self.stdout.write(f"LIVE_FAILURES={live_failures}")
        if routable == 0:
            self.stderr.write("CHAT_BLOCKED: no routable models")
