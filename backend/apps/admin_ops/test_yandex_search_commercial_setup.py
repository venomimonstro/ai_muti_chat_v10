from decimal import Decimal

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.procurement.models import ProviderFundingAccount, ProviderPurchase
from apps.procurement.services import account_available_native

from .live_tool_views import LiveToolSettingsView


@pytest.mark.django_db(transaction=True)
def test_yandex_search_setup_creates_funded_default_account_once():
    admin = User.objects.create_user(
        username="yandex-search-admin",
        email="yandex-search-admin@example.test",
        password="password123",
        role=User.Role.PLATFORM_ADMIN,
    )
    factory = APIRequestFactory()
    request = factory.patch(
        "/admin/live-tools/",
        {
            "api_key": "test-yandex-search-key",
            "folder_id": "folder-test",
            "region": "225",
            "search_type": "SEARCH_TYPE_RU",
            "markup_percent": "100",
            "purchased_requests": "1000",
            "purchase_cost_rub": "1500",
        },
        format="json",
    )
    force_authenticate(request, user=admin)
    response = LiveToolSettingsView.as_view()(request)
    assert response.status_code == 200

    provider = Provider.objects.get(slug="yandex-search")
    account = ProviderFundingAccount.objects.get(provider=provider, is_default=True)
    assert provider.enabled is True
    assert account.api_key_id is not None
    assert account.currency == "RUB"
    assert account.funded_native == Decimal("1000.000000")
    assert account_available_native(account) == Decimal("1000.000000")
    purchase = ProviderPurchase.objects.get(account=account)
    assert purchase.total_cash_outlay_rub == Decimal("1500.0000")
    assert purchase.effective_cost_rub_per_native == Decimal("1.50000000")

    second = factory.patch(
        "/admin/live-tools/",
        {
            "folder_id": "folder-test",
            "region": "225",
            "search_type": "SEARCH_TYPE_RU",
            "markup_percent": "120",
        },
        format="json",
    )
    force_authenticate(second, user=admin)
    response = LiveToolSettingsView.as_view()(second)
    assert response.status_code == 200
    assert ProviderPurchase.objects.filter(account=account).count() == 1
    provider.refresh_from_db()
    assert provider.auth_config["markup_percent"] == "120"


@pytest.mark.django_db(transaction=True)
def test_yandex_search_purchase_requires_both_quantity_and_cost():
    admin = User.objects.create_user(
        username="yandex-search-admin-invalid",
        email="yandex-search-admin-invalid@example.test",
        password="password123",
        role=User.Role.PLATFORM_ADMIN,
    )
    factory = APIRequestFactory()
    request = factory.patch(
        "/admin/live-tools/",
        {
            "api_key": "test-yandex-search-key-2",
            "folder_id": "folder-test",
            "region": "225",
            "search_type": "SEARCH_TYPE_RU",
            "markup_percent": "100",
            "purchased_requests": "1000",
        },
        format="json",
    )
    force_authenticate(request, user=admin)
    response = LiveToolSettingsView.as_view()(request)
    assert response.status_code == 400
    assert ProviderPurchase.objects.count() == 0
