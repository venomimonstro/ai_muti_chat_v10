from datetime import datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.billing.models import BalanceReservation, LedgerEntry

from .models import Notification, UserPreference

ZERO = Decimal("0.0000")


def _period_start(*, monthly=False):
    today = timezone.localdate()
    day = today.replace(day=1) if monthly else today
    return timezone.make_aware(datetime.combine(day, time.min))


def enforce_spend_limits(wallet, next_reservation):
    preference, _ = UserPreference.objects.get_or_create(user=wallet.user)
    preference = UserPreference.objects.select_for_update().get(pk=preference.pk)
    active_reserved = (
        wallet.reservations.filter(state=BalanceReservation.State.ACTIVE).aggregate(
            total=Sum("amount_rub")
        )["total"]
        or ZERO
    )
    checks = (
        (preference.daily_spend_limit_rub, _period_start(), "Достигнут дневной лимит расходов"),
        (
            preference.monthly_spend_limit_rub,
            _period_start(monthly=True),
            "Достигнут месячный лимит расходов",
        ),
    )
    for limit, start, message in checks:
        if limit is None:
            continue
        spent = (
            wallet.entries.filter(kind=LedgerEntry.Kind.DEBIT, created_at__gte=start).aggregate(
                total=Sum("amount_rub")
            )["total"]
            or ZERO
        )
        if spent + active_reserved + next_reservation > limit:
            raise ValidationError(message)


@transaction.atomic
def notify_low_balance(wallet):
    preference, _ = UserPreference.objects.get_or_create(user=wallet.user)
    if not preference.billing_notifications:
        return None
    if wallet.available_rub > preference.low_balance_threshold_rub:
        return None
    key = f"low-balance:{timezone.localdate().isoformat()}"
    notification, _ = Notification.objects.get_or_create(
        user=wallet.user,
        dedupe_key=key,
        defaults={
            "title": "Баланс заканчивается",
            "body": f"Доступно {wallet.available_rub:.2f} ₽. Пополните баланс, чтобы работа не прервалась.",
            "level": Notification.Level.WARNING,
            "action_url": "/?panel=wallet",
        },
    )
    return notification
