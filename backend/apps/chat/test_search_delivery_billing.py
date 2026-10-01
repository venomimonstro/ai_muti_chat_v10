from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.accounts.models import User
from apps.billing.models import BalanceReservation
from apps.billing.services import credit, reserve

from . import paid_search_billing, search_delivery_billing, streaming


@pytest.mark.django_db(transaction=True)
def test_undelivered_paid_search_context_releases_customer_reserve():
    user = User.objects.create_user(
        username="search-context-refund",
        email="search-context-refund@example.test",
        password="password123!",
    )
    credit(user, Decimal("20"), "test", "search-context-refund")
    reservation = reserve(user, Decimal("2.50"), "web-search:test-undelivered-context")

    token = paid_search_billing._usage.set(
        {
            "provider": "yandex",
            "paid": True,
            "provider_cost_rub": "1.00",
            "customer_charge_rub": "2.50",
            "customer_reservation_id": str(reservation.id),
        }
    )
    try:
        fake = SimpleNamespace(
            enrich_snapshot_with_web=lambda snapshot, query, required: {
                **snapshot,
                "web_search": {
                    "used": False,
                    "required": True,
                    "error": "context_budget_exhausted",
                },
            }
        )
        search_delivery_billing.install(
            streaming_module=fake,
            paid_search_module=paid_search_billing,
        )
        result = fake.enrich_snapshot_with_web({}, "актуальная цена", required=True)
    finally:
        usage = dict(paid_search_billing._usage.get() or {})
        paid_search_billing._usage.reset(token)

    reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert reservation.state == BalanceReservation.State.RELEASED
    assert reservation.actual_rub is None
    assert user.wallet.available_rub == Decimal("20.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert result["web_search"]["customer_charge_rub"] == "0"
    assert result["web_search"]["customer_refunded_reason"] == "search_context_not_delivered"
    assert "customer_reservation_id" not in usage


def test_search_delivery_billing_is_installed_on_real_streaming_runtime():
    assert getattr(
        streaming.enrich_snapshot_with_web,
        "_ai_workspace_search_delivery_billing",
        False,
    ) is True
