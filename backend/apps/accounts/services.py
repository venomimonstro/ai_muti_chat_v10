import os
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.billing.models import BalanceReservation, LedgerEntry

from .models import Notification, UserPreference

ZERO = Decimal("0.0000")
HUNDRED = Decimal("100")


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


def _positive_percent(name, default):
    value = _positive_env_limit(name, default)
    if value is None:
        return None
    return min(value, HUNDRED)


def _effective_limit(user_limit, system_limit):
    if user_limit is None:
        return system_limit
    if system_limit is None:
        return user_limit
    return min(user_limit, system_limit)


def _sum_debits(wallet, since):
    return (
        wallet.entries.filter(kind=LedgerEntry.Kind.DEBIT, created_at__gte=since).aggregate(
            total=Sum("amount_rub")
        )["total"]
        or ZERO
    )


def _active_reserved(wallet):
    return (
        wallet.reservations.filter(state=BalanceReservation.State.ACTIVE).aggregate(
            total=Sum("amount_rub")
        )["total"]
        or ZERO
    )


def spend_guard_snapshot(wallet):
    """Return the current consumer safety envelope without mutating the wallet.

    Limits deliberately use both absolute RUB ceilings and percentages of the
    customer's own funded balance. This prevents a pricing/routing bug from
    draining a large share of the wallet in one or two requests.
    """
    active_reserved = _active_reserved(wallet)
    total_funds_now = wallet.available_rub + active_reserved

    single_absolute = _positive_env_limit("CONSUMER_MAX_SINGLE_REQUEST_RUB", "250")
    single_percent = _positive_percent("CONSUMER_MAX_SINGLE_REQUEST_BALANCE_PERCENT", "10")
    percent_single = (
        (total_funds_now * single_percent / HUNDRED) if single_percent is not None else None
    )
    single_limit = _effective_limit(percent_single, single_absolute)

    burst_minutes = max(1, int(os.getenv("CONSUMER_BURST_WINDOW_MINUTES", "10")))
    burst_since = timezone.now() - timedelta(minutes=burst_minutes)
    burst_spent = _sum_debits(wallet, burst_since)
    # Add recent spend back to reconstruct the approximate balance that existed
    # before this burst. This keeps the percentage cap stable across sequential
    # requests instead of shrinking unpredictably after every debit.
    burst_basis = wallet.available_rub + active_reserved + burst_spent
    burst_absolute = _positive_env_limit("CONSUMER_MAX_BURST_SPEND_RUB", "500")
    burst_percent = _positive_percent("CONSUMER_MAX_BURST_SPEND_PERCENT", "20")
    percent_burst = (
        (burst_basis * burst_percent / HUNDRED) if burst_percent is not None else None
    )
    burst_limit = _effective_limit(percent_burst, burst_absolute)

    today_start = _period_start()
    spent_today = _sum_debits(wallet, today_start)
    daily_basis = wallet.available_rub + active_reserved + spent_today
    daily_absolute = _positive_env_limit("CONSUMER_MAX_DAILY_SPEND_RUB", "20000")
    daily_percent = _positive_percent("CONSUMER_MAX_DAILY_BALANCE_PERCENT", "50")
    percent_daily = (
        (daily_basis * daily_percent / HUNDRED) if daily_percent is not None else None
    )
    daily_system_limit = _effective_limit(percent_daily, daily_absolute)

    monthly_system_limit = _positive_env_limit("CONSUMER_MAX_MONTHLY_SPEND_RUB", "100000")

    return {
        "active_reserved_rub": active_reserved,
        "total_funds_now_rub": total_funds_now,
        "single_request_limit_rub": single_limit,
        "single_request_balance_percent": single_percent,
        "burst_window_minutes": burst_minutes,
        "burst_spent_rub": burst_spent,
        "burst_limit_rub": burst_limit,
        "burst_balance_percent": burst_percent,
        "spent_today_rub": spent_today,
        "daily_system_limit_rub": daily_system_limit,
        "daily_balance_percent": daily_percent,
        "monthly_system_limit_rub": monthly_system_limit,
    }


def enforce_spend_limits(wallet, next_reservation):
    next_reservation = Decimal(next_reservation)
    guard = spend_guard_snapshot(wallet)

    single_limit = guard["single_request_limit_rub"]
    if single_limit is not None and next_reservation > single_limit:
        raise ValidationError(
            f"Защитный лимит одного AI-запроса: не более {single_limit:.2f} ₽. "
            "Запрос остановлен до обращения к провайдеру, деньги не списаны."
        )

    preference, _ = UserPreference.objects.get_or_create(user=wallet.user)
    preference = UserPreference.objects.select_for_update().get(pk=preference.pk)
    active_reserved = guard["active_reserved_rub"]

    burst_limit = guard["burst_limit_rub"]
    if (
        burst_limit is not None
        and guard["burst_spent_rub"] + active_reserved + next_reservation > burst_limit
    ):
        raise ValidationError(
            f"Сработала защита от резкого расхода баланса: за {guard['burst_window_minutes']} мин. "
            f"можно потратить не более {burst_limit:.2f} ₽. Повторите запрос позже."
        )

    checks = (
        (
            _effective_limit(preference.daily_spend_limit_rub, guard["daily_system_limit_rub"]),
            _period_start(),
            "Достигнут дневной защитный лимит расходов",
        ),
        (
            _effective_limit(
                preference.monthly_spend_limit_rub,
                guard["monthly_system_limit_rub"],
            ),
            _period_start(monthly=True),
            "Достигнут месячный лимит расходов",
        ),
    )
    for limit, start, message in checks:
        if limit is None:
            continue
        spent = _sum_debits(wallet, start)
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
            "action_url": "/app/wallet",
        },
    )
    return notification
