import os
from datetime import datetime, time
from decimal import Decimal

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import LedgerEntry
from .services import credit

PROMO_LOCK_ID = 4_128_773_911
ZERO = Decimal("0")


def _lock_signup_promo_budget():
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [PROMO_LOCK_ID])


@transaction.atomic
def grant_signup_promo(user):
    amount = Decimal(str(settings.SIGNUP_PROMO_RUB))
    if amount <= 0:
        return None
    cap = Decimal(
        str(
            getattr(
                settings,
                "SIGNUP_PROMO_DAILY_CAP_RUB",
                os.getenv("SIGNUP_PROMO_DAILY_CAP_RUB", "1000.00"),
            )
        )
    )
    if cap <= 0:
        return None
    existing = LedgerEntry.objects.filter(
        idempotency_key=f"credit:signup_promo:signup:{user.id}"
    ).first()
    if existing:
        return existing

    _lock_signup_promo_budget()
    today = timezone.localdate()
    start = timezone.make_aware(
        datetime.combine(today, time.min),
        timezone.get_current_timezone(),
    )
    issued = (
        LedgerEntry.objects.filter(
            kind=LedgerEntry.Kind.CREDIT,
            source_type="signup_promo",
            created_at__gte=start,
        ).aggregate(total=Sum("amount_rub"))["total"]
        or ZERO
    )
    if issued + amount > cap:
        return None
    return credit(
        user,
        amount,
        "signup_promo",
        f"signup:{user.id}",
        bucket="promo",
    )
