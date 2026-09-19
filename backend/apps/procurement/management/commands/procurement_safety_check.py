import json
import os
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import MarginPolicyVersion
from apps.billing.pricing import active_fx_snapshot, active_margin_policy, active_price, active_retail_price

from ...models import ProviderFundingAccount, ProviderSpendReservation
from ...services import account_available_native, credential_is_configured


class Command(BaseCommand):
    help = "Fail-closed audit of provider procurement balances, credentials and FX margin risk."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--stress-usd-rub", type=Decimal, default=Decimal(os.getenv("PROCUREMENT_STRESS_USD_RUB", "200")))
        parser.add_argument("--stale-minutes", type=int, default=30)

    def handle(self, *args, **options):
        blockers = []
        warnings = []
        stress_usd = max(Decimal("1"), options["stress_usd_rub"])
        stale_before = timezone.now() - timedelta(minutes=max(5, options["stale_minutes"]))
        policy = active_margin_policy()

        enabled_providers = Provider.objects.filter(enabled=True, emergency_disabled=False).exclude(
            adapter_type=Provider.AdapterType.ECHO
        )
        for provider in enabled_providers:
            accounts = list(
                ProviderFundingAccount.objects.filter(provider=provider, active=True).order_by(
                    "-is_default", "priority", "created_at"
                )
            )
            default = next((item for item in accounts if item.is_default), None)
            if not accounts:
                blockers.append(f"provider_without_procurement_account={provider.slug}")
                continue
            if default is None:
                blockers.append(f"provider_without_default_procurement_account={provider.slug}")
                continue
            if provider.credential_env != default.credential_env:
                blockers.append(f"provider_credential_not_default_procurement_account={provider.slug}")
            if not credential_is_configured(default):
                blockers.append(f"provider_procurement_credential_missing={provider.slug}:{default.credential_env}")
            available = account_available_native(default)
            if available <= 0:
                blockers.append(f"provider_procurement_balance_empty={provider.slug}:{default.label}")
            elif available <= default.low_balance_native:
                warnings.append(f"provider_procurement_balance_low={provider.slug}:{default.label}:{available}")

            for model in AIModel.objects.filter(provider=provider, enabled=True):
                try:
                    price = active_price(model.slug)
                    fx = active_fx_snapshot(price.provider_currency)
                except Exception as exc:
                    blockers.append(f"provider_price_or_fx_invalid={model.slug}:{exc}")
                    continue
                retail = active_retail_price(model.slug)
                if retail is None:
                    continue
                input_native = (
                    price.input_price_per_million
                    if price.input_price_per_million is not None
                    else price.input_rub_per_million
                )
                output_native = (
                    price.output_price_per_million
                    if price.output_price_per_million is not None
                    else price.output_rub_per_million
                )
                live_fx = fx.rate
                stress_fx = stress_usd if price.provider_currency.upper() == "USD" else live_fx
                for direction, native_cost, sale in (
                    ("input", input_native, retail.input_rub_per_million),
                    ("output", output_native, retail.output_rub_per_million),
                ):
                    live_cost = native_cost * live_fx
                    stress_cost = native_cost * stress_fx
                    live_margin = (sale - live_cost) / sale * Decimal("100") if sale else Decimal("-999")
                    stress_margin = (sale - stress_cost) / sale * Decimal("100") if sale else Decimal("-999")
                    if live_margin < policy.minimum_gross_margin_percent:
                        blockers.append(
                            f"live_margin_below_floor={model.slug}:{direction}:{live_margin.quantize(Decimal('0.001'))}"
                        )
                    if stress_margin <= 0:
                        blockers.append(
                            f"stress_fx_unprofitable={model.slug}:{direction}:usd_rub={stress_usd}"
                        )
                    elif stress_margin < policy.minimum_gross_margin_percent:
                        warnings.append(
                            f"stress_fx_margin_below_floor={model.slug}:{direction}:{stress_margin.quantize(Decimal('0.001'))}"
                        )

        stale = ProviderSpendReservation.objects.filter(
            state=ProviderSpendReservation.State.ACTIVE,
            created_at__lt=stale_before,
        ).count()
        if stale:
            blockers.append(f"stale_provider_spend_reservations={stale}")

        for account in ProviderFundingAccount.objects.select_related("provider").all():
            if account.reserved_native < 0 or account.spent_native < 0 or account.funded_native < 0:
                blockers.append(f"invalid_procurement_account_balance={account.id}")
            if account.reserved_native + account.spent_native > account.funded_native:
                blockers.append(f"procurement_account_overdrawn={account.id}")

        payload = {
            "ok": not blockers,
            "stress_usd_rub": str(stress_usd),
            "margin_floor_percent": str(policy.minimum_gross_margin_percent),
            "blockers": blockers,
            "warnings": warnings,
            "checked_at": timezone.now().isoformat(),
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False))
        else:
            for item in warnings:
                self.stdout.write(self.style.WARNING(f"WARN: {item}"))
            for item in blockers:
                self.stdout.write(self.style.ERROR(f"BLOCK: {item}"))
        if blockers:
            raise CommandError("Procurement safety check failed: " + "; ".join(blockers))
        if not options["as_json"]:
            self.stdout.write(self.style.SUCCESS("PROCUREMENT SAFETY: PASS"))
