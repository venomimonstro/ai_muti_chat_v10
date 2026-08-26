from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.accounts.models import User

from .models import BalanceReservation, PriceVersion
from .pricing import calculate
from .services import credit, reconstruct, release, reserve, settle


@pytest.mark.django_db(transaction=True)
def test_release_and_settle_are_idempotent():
    user = User.objects.create_user(username="money", password="password123")
    credit(user, Decimal("5"), "test", "idempotency")
    first = reserve(user, Decimal("1"), "reserve:release")
    release(first.id)
    release(first.id)
    second = reserve(user, Decimal("1"), "reserve:settle")
    settle(second.id, Decimal("0.25"))
    settle(second.id, Decimal("0.25"))
    user.wallet.refresh_from_db()
    assert reconstruct(user.wallet) == (Decimal("4.7500"), Decimal("0.0000"))
    assert first.__class__.objects.get(id=first.id).state == BalanceReservation.State.RELEASED


@pytest.mark.django_db
def test_historical_price_version_is_immutable_and_reproducible():
    version = PriceVersion.objects.create(
        model_slug="model-v1",
        input_rub_per_million=Decimal("100"),
        output_rub_per_million=Decimal("200"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    assert calculate(version, 1_000, 500) == (Decimal("0.2000"), Decimal("0.4000"))
    version.markup_percent = Decimal("50")
    with pytest.raises(ValidationError):
        version.save()


@pytest.mark.django_db(transaction=True)
def test_parallel_reserves_never_make_postgres_balance_negative():
    if connection.vendor != "postgresql":
        pytest.skip("Real row-lock test requires PostgreSQL")
    user = User.objects.create_user(username="parallel", password="password123")
    credit(user, Decimal("10"), "test", "parallel")

    def attempt(number):
        close_old_connections()
        try:
            reserve(user, Decimal("1"), f"parallel:{number}")
            return True
        except ValidationError:
            return False
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))

    user.wallet.refresh_from_db()
    assert sum(results) == 10
    assert user.wallet.available_rub == Decimal("0.0000")
    assert user.wallet.reserved_rub == Decimal("10.0000")
    assert reconstruct(user.wallet) == (Decimal("0.0000"), Decimal("10.0000"))
