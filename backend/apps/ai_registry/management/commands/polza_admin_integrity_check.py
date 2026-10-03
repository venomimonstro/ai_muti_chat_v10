from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.client_readiness import provider_model_config_ready
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.billing.models import MarkupRuleVersion
from apps.billing.pricing import _effective_rules, active_price
from apps.procurement.models import ProviderPurchase


class Command(BaseCommand):
    help = "Audit Polza key allowlists, batch procurement snapshots, prices and effective markup."

    def handle(self, *args, **options):
        provider = Provider.objects.filter(slug="polza").first()
        if provider is None:
            raise CommandError("POLZA_PROVIDER_MISSING")

        failures = []
        keys = list(
            ProviderApiKey.objects.filter(provider=provider, enabled=True)
            .order_by("priority", "created_at")
        )
        for key in keys:
            available = {str(x) for x in (key.available_models or []) if x}
            allowed = {str(x) for x in (key.allowed_models or []) if x}
            invalid = sorted(allowed - available) if available else []
            self.stdout.write(
                "POLZA_KEY_POLICY "
                f"id={key.id} label={key.label!r} health={key.health_state} "
                f"available={len(available)} allowed={len(allowed)} invalid={len(invalid)}"
            )
            if invalid:
                failures.append(f"key:{key.id}:invalid_allowlist={invalid[:10]}")

        purchases = list(
            ProviderPurchase.objects.filter(account__provider=provider)
            .exclude(pricing_snapshot={})
            .order_by("-purchased_at", "-created_at")[:20]
        )
        for purchase in purchases:
            snapshot = purchase.pricing_snapshot or {}
            models = snapshot.get("models") if isinstance(snapshot.get("models"), list) else []
            if models:
                self.stdout.write(
                    "POLZA_BATCH_ORDER "
                    f"document={purchase.document_number} models={len(models)} "
                    f"provider_markup={snapshot.get('provider_markup_percent') or '-'}"
                )

        models = list(
            AIModel.objects.filter(provider=provider)
            .select_related("provider", "current_version")
            .order_by("slug")
        )
        for model in models:
            allowed_keys = [
                key
                for key in keys
                if model.upstream_model in (key.allowed_models or [])
                and key.health_state == ProviderApiKey.HealthState.HEALTHY
            ]
            try:
                price = active_price(model.slug)
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(
                        "POLZA_MODEL_PRICE_MISSING "
                        f"model={model.slug} upstream={model.upstream_model!r} detail={str(exc)[:200]}"
                    )
                )
                if model.enabled:
                    failures.append(f"model:{model.slug}:missing_price")
                continue

            markup, multiplier, rules = _effective_rules(
                price=price,
                provider_slug=provider.slug,
                model_slug=model.slug,
            )
            provider_rule = next(
                (
                    rule for rule in rules
                    if rule.get("scope_type") == MarkupRuleVersion.Scope.PROVIDER
                ),
                None,
            )
            model_rule = next(
                (
                    rule for rule in rules
                    if rule.get("scope_type") == MarkupRuleVersion.Scope.MODEL
                ),
                None,
            )
            self.stdout.write(
                "POLZA_MODEL_ECONOMICS "
                f"model={model.slug} upstream={model.upstream_model!r} enabled={model.enabled} "
                f"allowed_keys={len(allowed_keys)} config_ready={provider_model_config_ready(model)} "
                f"cost_in={price.input_price_per_million} cost_out={price.output_price_per_million} "
                f"currency={price.provider_currency} effective_markup={markup} multiplier={multiplier} "
                f"provider_rule={provider_rule or '-'} model_rule={model_rule or '-'}"
            )
            if model.enabled and not allowed_keys:
                failures.append(f"model:{model.slug}:no_allowed_healthy_key")

        if failures:
            raise CommandError(
                "POLZA_ADMIN_INTEGRITY_BROKEN " + " | ".join(failures[:50])
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"POLZA_ADMIN_INTEGRITY_OK keys={len(keys)} models={len(models)} purchases={len(purchases)}"
            )
        )
