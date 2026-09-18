from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import FxRateSnapshot
from .pricing import active_fx_snapshot


@pytest.mark.django_db
def test_stale_non_rub_fx_snapshot_fails_closed(monkeypatch):
    monkeypatch.setenv("FX_RATE_MAX_AGE_HOURS", "24")
    FxRateSnapshot.objects.create(
        base_currency="USD",
        quote_currency="RUB",
        rate=Decimal("90"),
        source="test",
        effective_at=timezone.now() - timedelta(hours=25),
    )

    with pytest.raises(ValidationError, match="устарел"):
        active_fx_snapshot("USD")


@pytest.mark.django_db
def test_fresh_non_rub_fx_snapshot_is_usable(monkeypatch):
    monkeypatch.setenv("FX_RATE_MAX_AGE_HOURS", "24")
    snapshot = FxRateSnapshot.objects.create(
        base_currency="USD",
        quote_currency="RUB",
        rate=Decimal("91"),
        source="test",
        effective_at=timezone.now() - timedelta(hours=2),
    )

    assert active_fx_snapshot("USD").id == snapshot.id


@pytest.mark.django_db
def test_rub_identity_fx_does_not_expire(monkeypatch):
    monkeypatch.setenv("FX_RATE_MAX_AGE_HOURS", "1")
    snapshot = active_fx_snapshot("RUB")
    assert snapshot.rate == Decimal("1")
