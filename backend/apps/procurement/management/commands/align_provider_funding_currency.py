from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.models import Provider
from apps.billing.models import PriceVersion
from apps.procurement.models import ProviderFundingAccount


class Command(BaseCommand):
    help = "Align funding-account currency with the single active pricing currency for a provider."

    def add_arguments(self, parser):
        parser.add_argument("--provider", required=True)
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        slug = str(options["provider"] or "").strip()
        provider = Provider.objects.filter(slug=slug).first()
        if provider is None:
            raise CommandError(f"Provider not found: {slug}")

        model_slugs = list(provider.models.values_list("slug", flat=True))
        currencies = sorted({
            str(value or "").upper().strip()
            for value in PriceVersion.objects.filter(
                model_slug__in=model_slugs,
                active=True,
                effective_from__lte=timezone.now(),
            ).values_list("provider_currency", flat=True)
            if value
        })
        if len(currencies) != 1:
            raise CommandError(
                f"Expected exactly one active pricing currency for {slug}; found {currencies}"
            )
        target = currencies[0]
        accounts = list(
            ProviderFundingAccount.objects.select_for_update()
            .filter(provider=provider)
            .order_by("priority", "created_at")
        )
        if not accounts:
            self.stdout.write(f"NO_ACCOUNTS provider={slug} target_currency={target}")
            return

        changed = []
        for account in accounts:
            self.stdout.write(
                f"ACCOUNT id={account.id} current={account.currency} target={target} "
                f"funded={account.funded_native} reserved={account.reserved_native} spent={account.spent_native}"
            )
            if account.currency == target:
                continue
            if options["apply"]:
                # Numeric ledger amounts were already calculated from the active
                # PriceVersion currency. This repairs the metadata label that older
                # admin forms defaulted to USD even for RUB-priced providers.
                ProviderFundingAccount.objects.filter(pk=account.pk).update(
                    currency=target,
                    updated_at=timezone.now(),
                )
                changed.append(str(account.id))

        if not options["apply"]:
            transaction.set_rollback(True)
            self.stdout.write(
                self.style.WARNING(
                    f"DRY_RUN provider={slug} target_currency={target} mismatches="
                    f"{sum(1 for a in accounts if a.currency != target)}"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"ALIGNED provider={slug} target_currency={target} changed={len(changed)} ids={changed}"
            )
        )
