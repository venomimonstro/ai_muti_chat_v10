from decimal import Decimal

import pytest

from apps.ai_registry.models import Provider, ProviderApiKey
from apps.procurement.models import ProviderFundingAccount, ProviderPurchase


@pytest.mark.django_db
def test_new_purchase_reopens_bound_degraded_key_for_controlled_probe():
    provider = Provider.objects.create(
        slug="yandex-search-recovery-test",
        name="Yandex Search Recovery Test",
        enabled=True,
        health_state=Provider.HealthState.OPEN,
    )
    key = ProviderApiKey(provider=provider, label="funded", enabled=True, health_state=ProviderApiKey.HealthState.DEGRADED)
    key.set_secret("test-secret")
    key.save()
    account = ProviderFundingAccount.objects.create(
        provider=provider,
        api_key=key,
        label="main",
        active=True,
        is_default=True,
        native_currency="UNIT",
    )

    ProviderPurchase.objects.create(
        account=account,
        native_amount=Decimal("100"),
        paid_rub=Decimal("100"),
        source="manual",
    )

    provider.refresh_from_db()
    key.refresh_from_db()
    assert provider.health_state == Provider.HealthState.UNKNOWN
    assert key.health_state == ProviderApiKey.HealthState.UNKNOWN
    assert key.last_error_code == ""
