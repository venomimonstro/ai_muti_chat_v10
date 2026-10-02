from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.ai_registry.models import Provider, ProviderApiKey
from apps.procurement.models import ProviderFundingAccount
from apps.procurement.services import record_purchase


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
        currency="USD",
    )
    operator = User.objects.create_user(
        username="purchase-recovery-operator",
        email="purchase-recovery@example.test",
        password="test-pass-123",
    )

    record_purchase(
        account=account,
        credit_native=Decimal("100"),
        base_cost_rub=Decimal("100"),
        created_by=operator,
        reference="recovery regression",
    )

    provider.refresh_from_db()
    key.refresh_from_db()
    assert provider.health_state == Provider.HealthState.UNKNOWN
    assert key.health_state == ProviderApiKey.HealthState.UNKNOWN
    assert key.last_error_code == ""
