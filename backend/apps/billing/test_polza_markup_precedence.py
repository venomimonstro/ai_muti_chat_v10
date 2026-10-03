from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.models import MarkupRuleVersion, PriceVersion
from apps.billing.pricing import _effective_rules


@pytest.mark.django_db
def test_polza_model_markup_overrides_provider_markup():
    price = PriceVersion.objects.create(
        model_slug="polza-gpt-61-sol",
        input_rub_per_million=Decimal("118.33"),
        output_rub_per_million=Decimal("591.64"),
        provider_currency="RUB",
        input_price_per_million=Decimal("118.33"),
        output_price_per_million=Decimal("591.64"),
        markup_percent=Decimal("100"),
        active=True,
        effective_from=timezone.now(),
    )
    MarkupRuleVersion.objects.create(
        scope_type=MarkupRuleVersion.Scope.PROVIDER,
        scope_key="polza",
        markup_percent=Decimal("200"),
        price_multiplier=Decimal("1"),
        effective_from=timezone.now(),
    )
    MarkupRuleVersion.objects.create(
        scope_type=MarkupRuleVersion.Scope.MODEL,
        scope_key=price.model_slug,
        markup_percent=Decimal("250"),
        price_multiplier=Decimal("1"),
        effective_from=timezone.now(),
    )

    markup, multiplier, _rules = _effective_rules(
        price=price,
        provider_slug="polza",
        model_slug=price.model_slug,
    )
    assert markup == Decimal("250")
    assert multiplier == Decimal("1")


@pytest.mark.django_db
def test_blank_model_rule_resets_to_provider_markup():
    price = PriceVersion.objects.create(
        model_slug="polza-gpt-reset",
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        provider_currency="RUB",
        input_price_per_million=Decimal("10"),
        output_price_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        active=True,
        effective_from=timezone.now(),
    )
    MarkupRuleVersion.objects.create(
        scope_type=MarkupRuleVersion.Scope.PROVIDER,
        scope_key="polza",
        markup_percent=Decimal("200"),
        price_multiplier=Decimal("1"),
        effective_from=timezone.now(),
    )
    MarkupRuleVersion.objects.create(
        scope_type=MarkupRuleVersion.Scope.MODEL,
        scope_key=price.model_slug,
        markup_percent=Decimal("300"),
        price_multiplier=Decimal("1"),
        effective_from=timezone.now(),
    )
    MarkupRuleVersion.objects.create(
        scope_type=MarkupRuleVersion.Scope.MODEL,
        scope_key=price.model_slug,
        markup_percent=None,
        price_multiplier=Decimal("1"),
        effective_from=timezone.now(),
        reason="reset",
    )

    markup, _multiplier, _rules = _effective_rules(
        price=price,
        provider_slug="polza",
        model_slug=price.model_slug,
    )
    assert markup == Decimal("200")
