from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry import dispatch
from apps.ai_registry.client_readiness import provider_model_config_ready
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.ai_registry.reliability import model_client_ready, provider_available
from apps.billing.pricing import active_price
from apps.procurement.models import ProviderPurchase


class Command(BaseCommand):
    help = "Validate Polza key catalogs, configured models, pricing, readiness and optional live inference."

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--all-models", action="store_true")
        parser.add_argument(
            "--pricing",
            action="store_true",
            help="Also verify current Polza procurement pricing for every enabled key.",
        )

    def handle(self, *args, **options):
        provider = Provider.objects.filter(slug="polza").first()
        if provider is None:
            raise CommandError("POLZA_PROVIDER_MISSING")

        self.stdout.write(
            "POLZA_PROVIDER "
            f"enabled={provider.enabled} health={provider.health_state} "
            f"available={provider_available(provider)} base_url={provider.api_base_url}"
        )

        keys = list(
            ProviderApiKey.objects.filter(provider=provider, enabled=True)
            .order_by("priority", "created_at")
        )
        if not keys:
            raise CommandError("POLZA_KEY_MISSING")

        for key in keys:
            self.stdout.write(
                "POLZA_KEY "
                f"id={key.id} label={key.label!r} health={key.health_state} "
                f"catalog_models={len(key.available_models or [])} allowed_models={len(key.allowed_models or [])} "
                f"last_error={key.last_error_code or '-'}"
            )

        if options.get("pricing"):
            from apps.admin_ops.procurement_ledger_views import _polza_pricing_for_key

            for key in keys:
                if key.health_state != ProviderApiKey.HealthState.HEALTHY:
                    continue
                try:
                    price_rows = _polza_pricing_for_key(key)
                    priced = sum(
                        1
                        for row in price_rows
                        if any(
                            row.get(field) not in (None, "")
                            for field in (
                                "input_per_million",
                                "output_per_million",
                                "image_input_per_million",
                                "image_output_per_million",
                                "image_per_image",
                            )
                        )
                    )
                    manual = len(price_rows) - priced
                    self.stdout.write(
                        "POLZA_PRICING "
                        f"key_id={key.id} models={len(price_rows)} "
                        f"auto_priced={priced} manual_required={manual}"
                    )
                except Exception as exc:
                    raise CommandError(
                        "POLZA_PRICING_BROKEN "
                        f"key_id={key.id} type={type(exc).__name__} detail={str(exc)[:500]}"
                    ) from exc

        try:
            from apps.admin_ops.procurement_ledger_views import _polza_pricing_for_key

            for key in keys:
                if key.health_state != ProviderApiKey.HealthState.HEALTHY:
                    continue
                rows = _polza_pricing_for_key(key)
                priced = [
                    row
                    for row in rows
                    if any(
                        row.get(field) not in (None, "")
                        for field in (
                            "input_per_million",
                            "output_per_million",
                            "image_input_per_million",
                            "image_output_per_million",
                            "image_per_image",
                        )
                    )
                ]
                self.stdout.write(
                    "POLZA_PRICING "
                    f"key_id={key.id} catalog_models={len(rows)} priced_models={len(priced)}"
                )
        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(
                    "POLZA_PRICING_WARN "
                    f"type={type(exc).__name__} detail={str(exc)[:300]}"
                )
            )

        latest_purchase = (
            ProviderPurchase.objects.filter(account__provider=provider)
            .exclude(pricing_snapshot={})
            .select_related("account__api_key")
            .order_by("-purchased_at", "-created_at")
            .first()
        )
        if latest_purchase is not None:
            snapshot = latest_purchase.pricing_snapshot or {}
            self.stdout.write(
                "POLZA_PURCHASE_PRICE_SNAPSHOT "
                f"document={latest_purchase.document_number} "
                f"model={snapshot.get('upstream_model') or '-'} "
                f"input={snapshot.get('input_per_million') or '-'} "
                f"output={snapshot.get('output_per_million') or '-'} "
                f"image_input={snapshot.get('image_input_per_million') or '-'} "
                f"image_output={snapshot.get('image_output_per_million') or '-'} "
                f"image_per_image={snapshot.get('image_per_image') or '-'} "
                f"sources={snapshot.get('sources') or {}}"
            )

        models = list(
            AIModel.objects.filter(provider=provider)
            .select_related("provider", "current_version")
            .order_by("enabled", "slug")
        )
        if not models:
            self.stdout.write(self.style.WARNING("POLZA_MODELS_EMPTY"))
            return

        failures = []
        live_checked = 0
        for model in models:
            price_ok = True
            price_error = ""
            try:
                active_price(model.slug)
            except Exception as exc:
                price_ok = False
                price_error = str(exc)

            compatible_keys = [
                key
                for key in keys
                if key.health_state == ProviderApiKey.HealthState.HEALTHY
                and (
                    model.upstream_model in list(key.allowed_models or [])
                )
            ]
            config_ready = provider_model_config_ready(model)
            client_ready = model_client_ready(model)
            active = None
            if price_ok:
                try:
                    active = active_price(model.slug)
                except Exception:
                    active = None
            self.stdout.write(
                "POLZA_MODEL "
                f"slug={model.slug} upstream={model.upstream_model!r} "
                f"enabled={model.enabled} compatible_keys={len(compatible_keys)} "
                f"config_ready={config_ready} price_ok={price_ok} "
                f"client_ready={client_ready} "
                f"price_currency={getattr(active, 'provider_currency', '-') if active else '-'} "
                f"price_in={getattr(active, 'input_price_per_million', '-') if active else '-'} "
                f"price_out={getattr(active, 'output_price_per_million', '-') if active else '-'} "
                f"price_error={price_error or '-'}"
            )

            if model.enabled and (not compatible_keys or not price_ok or not client_ready):
                failures.append(model.slug)

            if options["live"] and model.enabled and client_ready:
                if live_checked and not options["all_models"]:
                    continue
                try:
                    adapter = dispatch.adapter_for(
                        model,
                        allow_probe=False,
                        require_funding_balance=False,
                    )
                    result = adapter.generate(
                        model=model.upstream_model,
                        messages=[{"role": "user", "content": "Ответь только: OK"}],
                        max_output_tokens=8,
                    )
                    if not str(result.text or "").strip():
                        raise RuntimeError("empty response")
                    self.stdout.write(
                        self.style.SUCCESS(
                            "POLZA_LIVE_OK "
                            f"model={model.slug} key_id={getattr(adapter, '_ai_workspace_key_id', '')} "
                            f"input_tokens={result.input_tokens} output_tokens={result.output_tokens}"
                        )
                    )
                    live_checked += 1
                except Exception as exc:
                    failures.append(model.slug)
                    self.stdout.write(
                        self.style.ERROR(
                            "POLZA_LIVE_FAIL "
                            f"model={model.slug} type={type(exc).__name__} "
                            f"code={getattr(exc, 'code', '-')} detail={str(exc)[:500]}"
                        )
                    )

        if failures:
            unique = sorted(set(failures))
            raise CommandError(f"POLZA_INTEGRATION_BROKEN models={unique}")

        self.stdout.write(
            self.style.SUCCESS(
                f"POLZA_INTEGRATION_OK keys={len(keys)} models={len(models)} live_checked={live_checked}"
            )
        )
