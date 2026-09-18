import os
from datetime import datetime, time
from decimal import Decimal, InvalidOperation

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


def _positive_env_limit(name, default):
    try:
        value = Decimal(os.getenv(name, default))
    except (InvalidOperation, TypeError):
        value = Decimal(default)
    return value if value > 0 else None


def _effective_limit(user_limit, system_limit):
    if user_limit is None:
        return system_limit
    if system_limit is None:
        return user_limit
    return min(user_limit, system_limit)


def enforce_spend_limits(wallet, next_reservation):
    next_reservation = Decimal(next_reservation)
    single_limit = _positive_env_limit("CONSUMER_MAX_SINGLE_REQUEST_RUB", "5000")
    daily_system_limit = _positive_env_limit("CONSUMER_MAX_DAILY_SPEND_RUB", "20000")
    monthly_system_limit = _positive_env_limit("CONSUMER_MAX_MONTHLY_SPEND_RUB", "100000")

    if single_limit is not None and next_reservation > single_limit:
        raise ValidationError("Запрос превышает системный лимит стоимости одной операции")

    preference, _ = UserPreference.objects.get_or_create(user=wallet.user)
    preference = UserPreference.objects.select_for_update().get(pk=preference.pk)
    active_reserved = (
        wallet.reservations.filter(state=BalanceReservation.State.ACTIVE).aggregate(
            total=Sum("amount_rub")
        )["total"]
        or ZERO
    )
    checks = (
        (
            _effective_limit(preference.daily_spend_limit_rub, daily_system_limit),
            _period_start(),
            "Достигнут дневной лимит расходов",
        ),
        (
            _effective_limit(preference.monthly_spend_limit_rub, monthly_system_limit),
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
