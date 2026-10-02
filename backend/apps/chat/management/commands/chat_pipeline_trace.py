from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.ai_registry.dispatch import adapter_for, runtime_credential_ready, select_runtime_api_key
from apps.ai_registry.models import AIModel, Provider, RoutingPolicyVersion, RoutingTierAssignment
from apps.ai_registry.routing_pools import tier_pool
from apps.ai_registry.reliability import model_client_ready, provider_available
from apps.billing.pricing import active_price, quote
from apps.procurement.account_routing import account_available_native, account_credential_ready
from apps.procurement.models import ProviderFundingAccount, ProviderSpendReservation
from apps.procurement.readiness import quote_has_procurement_capacity


class Command(BaseCommand):
    help = "Trace every production chat dependency without exposing credentials."

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true")

    def handle(self, *args, **options):
        live = bool(options["live"])
        self.stdout.write("=== CHAT PIPELINE TRACE ===")

        self.stdout.write("\n[1] PROVIDERS / KEYS")
        for provider in Provider.objects.all().order_by("priority", "slug"):
            _secret, key_id = select_runtime_api_key(
                provider,
                allow_probe=False,
                touch=False,
                require_funding_balance=False,
            )
            self.stdout.write(
                "PROVIDER "
                f"slug={provider.slug} enabled={provider.enabled} emergency={provider.emergency_disabled} "
                f"health={provider.health_state} credential_configured={provider.credential_configured()} "
                f"runtime_credential_ready={runtime_credential_ready(provider)} "
                f"provider_available={provider_available(provider)} selected_key_id={key_id or '-'}"
            )
            for key in provider.api_keys.all().order_by("priority", "created_at"):
                self.stdout.write(
                    "  KEY "
                    f"id={key.id} label={key.label!r} enabled={key.enabled} "
                    f"health={key.health_state} last_error={key.last_error_code or '-'} "
                    f"secret_present={bool(key.get_secret())}"
                )

        self.stdout.write("\n[2] PROCUREMENT ACCOUNTS / PURCHASED BALANCE")
        for account in (
            ProviderFundingAccount.objects.select_related("provider", "api_key")
            .all()
            .order_by("provider__priority", "provider__slug", "priority")
        ):
            self.stdout.write(
                "ACCOUNT "
                f"id={account.id} provider={account.provider.slug} label={account.label!r} "
                f"active={account.active} default={account.is_default} currency={account.currency} "
                f"funded={account.funded_native} reserved={account.reserved_native} "
                f"spent={account.spent_native} available={account_available_native(account)} "
                f"credential_ready={account_credential_ready(account)} "
                f"api_key_id={account.api_key_id or '-'} "
                f"api_key_health={getattr(account.api_key, 'health_state', '-') if account.api_key_id else '-'}"
            )
        active_reservations = ProviderSpendReservation.objects.filter(
            state=ProviderSpendReservation.State.ACTIVE
        ).count()
        self.stdout.write(f"ACTIVE_PROVIDER_RESERVATIONS={active_reservations}")

        self.stdout.write("\n[3] EFFECTIVE ROUTING TIERS")
        policy = RoutingPolicyVersion.objects.filter(active=True).first()
        thresholds = (policy.thresholds or {}) if policy else {}
        effective_pools = {}
        for tier in (
            RoutingTierAssignment.Tier.SIMPLE,
            RoutingTierAssignment.Tier.MEDIUM,
            RoutingTierAssignment.Tier.COMPLEX,
        ):
            pool = tier_pool(thresholds, tier)
            effective_pools[tier] = pool
            source = "db" if RoutingTierAssignment.objects.filter(enabled=True).exists() else "policy"
            self.stdout.write(
                f"TIER {tier} source={source} models={pool}"
            )

        self.stdout.write("\n[4] MODEL READINESS / PRICE / PROCUREMENT")
        routed_slugs = {
            slug
            for pool in effective_pools.values()
            for slug in pool
        }
        models = (
            AIModel.objects.filter(enabled=True)
            .select_related("provider")
            .order_by("provider__priority", "provider__slug", "slug")
        )
        for model in models:
            if routed_slugs and model.slug not in routed_slugs:
                continue
            price_ok = False
            margin_allowed = False
            procurement_ok = False
            quote_error = ""
            try:
                price = active_price(model.slug)
                value = quote(
                    price,
                    64,
                    min(256, model.max_output_tokens),
                    provider_slug=model.provider.slug,
                    model_slug=model.slug,
                )
                price_ok = True
                margin_allowed = value.margin_allowed
                procurement_ok = quote_has_procurement_capacity(model.provider, value)
            except Exception as exc:
                quote_error = f"{type(exc).__name__}:{exc}"
            self.stdout.write(
                "MODEL "
                f"slug={model.slug} provider={model.provider.slug} upstream={model.upstream_model!r} "
                f"client_ready={model_client_ready(model)} provider_available={provider_available(model.provider)} "
                f"runtime_credential_ready={runtime_credential_ready(model.provider)} "
                f"price_ok={price_ok} margin_allowed={margin_allowed} procurement_ok={procurement_ok} "
                f"quote_error={quote_error or '-'}"
            )

            if live and provider_available(model.provider):
                try:
                    adapter = adapter_for(
                        model,
                        allow_probe=False,
                        require_funding_balance=False,
                    )
                    result = adapter.generate(
                        model=model.upstream_model,
                        messages=[{"role": "user", "content": "Ответь только словом OK"}],
                        max_output_tokens=16,
                    )
                    self.stdout.write(
                        "  LIVE_OK "
                        f"provider={model.provider.slug} model={model.slug} "
                        f"input_tokens={result.input_tokens} output_tokens={result.output_tokens}"
                    )
                except Exception as exc:
                    self.stdout.write(
                        self.style.ERROR(
                            "  LIVE_FAIL "
                            f"provider={model.provider.slug} model={model.slug} "
                            f"type={type(exc).__name__} code={getattr(exc, 'code', '-')} detail={str(exc)[:500]}"
                        )
                    )

        self.stdout.write("\n[5] RESULT")
        simple_ready = []
        simple_pool = effective_pools.get(RoutingTierAssignment.Tier.SIMPLE, [])
        for model in (
            AIModel.objects.filter(slug__in=simple_pool, enabled=True)
            .select_related("provider")
            .order_by("provider__priority", "slug")
        ):
            if model_client_ready(model):
                simple_ready.append(model.slug)
        self.stdout.write(f"SIMPLE_READY_MODELS={simple_ready}")
        if not simple_ready:
            self.stdout.write(self.style.ERROR("CHAT_PIPELINE_BROKEN: SIMPLE tier has no client-ready model"))
        else:
            self.stdout.write(self.style.SUCCESS("CHAT_PIPELINE_BASE_READY"))
