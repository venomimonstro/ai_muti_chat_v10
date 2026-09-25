import io
import os
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import Notification, SupportRequest, User

from .recovery import recover_stale_operations

WORKER_HEARTBEAT_KEY = "system:celery-worker-heartbeat"


@shared_task
def system_heartbeat_task():
    now = timezone.now().isoformat()
    cache.set(WORKER_HEARTBEAT_KEY, now, timeout=180)
    return now


@shared_task
def recover_stale_operations_task():
    result = recover_stale_operations()
    from apps.agents.recovery import expire_stale_agent_approvals, recover_stale_agent_runs

    result["agent_runs"] = recover_stale_agent_runs()
    result["agent_approvals"] = expire_stale_agent_approvals()
    return result


@shared_task
def payment_reconciliation_task():
    if not settings.PAYMENTS_ENABLED:
        return {"status": "disabled"}
    from apps.payments.services import reconcile_open_payments, reconcile_open_refunds

    payments = reconcile_open_payments()
    refunds = reconcile_open_refunds()
    result = {
        "status": "ok" if payments.error_count == 0 and refunds["errors"] == 0 else "errors",
        "payments_checked": payments.checked_count,
        "payments_corrected": payments.corrected_count,
        "payments_errors": payments.error_count,
        "refunds_checked": refunds["checked"],
        "refunds_corrected": refunds["corrected"],
        "refunds_errors": refunds["errors"],
    }
    if result["status"] != "ok":
        raise RuntimeError(f"payment reconciliation has errors: {result}")
    return result


@shared_task
def official_pricing_sync_task():
    """Refresh provider list prices and USD/RUB from official sources.

    The sync fails closed: if an official page no longer confirms a configured
    baseline price, no replacement PriceVersion is activated for that model.
    """
    from apps.procurement.official_pricing import sync_official_prices

    result = sync_official_prices()
    if result["rejected"] or result["expired"]:
        bucket = timezone.now().strftime("%Y-%m-%d")
        _notify_platform_admins(
            dedupe_key=f"official-pricing-sync:{bucket}",
            title="Проверьте официальные цены AI",
            body=(
                f"Подтверждено моделей: {len(result['verified'])}; "
                f"не подтверждено: {len(result['rejected'])}; "
                f"истёкших временных тарифов: {len(result['expired'])}. "
                "Коммерческие цены требуют проверки."
            ),
            action_url="/admin-console/procurement",
            level=Notification.Level.WARNING,
        )
    return result


@shared_task
def detect_abuse_task():
    output = io.StringIO()
    call_command("detect_abuse", stdout=output)
    return output.getvalue().strip()


def _notify_platform_admins(*, dedupe_key, title, body, action_url, level=Notification.Level.WARNING):
    created = 0
    admins = User.objects.filter(
        role=User.Role.PLATFORM_ADMIN,
        status=User.Status.ACTIVE,
    ).only("id")
    for admin in admins.iterator():
        _item, was_created = Notification.objects.get_or_create(
            user=admin,
            dedupe_key=dedupe_key,
            defaults={
                "title": title,
                "body": body,
                "level": level,
                "action_url": action_url,
            },
        )
        created += int(was_created)
    return created


@shared_task
def economic_safety_watch_task():
    output = io.StringIO()
    try:
        call_command("economic_safety_check", stdout=output)
    except CommandError as exc:
        bucket = timezone.now().strftime("%Y-%m-%d-%H")
        _notify_platform_admins(
            dedupe_key=f"economic-safety:{bucket}",
            title="Критическая проверка экономики не пройдена",
            body=(
                "Обнаружен риск отрицательного баланса, зависших резервов, "
                "убыточного провайдера или неограниченного API. "
                "Новые расходы необходимо проверить немедленно."
            ),
            action_url="/admin-console/finance",
            level=Notification.Level.WARNING,
        )
        raise RuntimeError(f"economic_safety_check failed: {exc}") from exc
    return output.getvalue().strip()


@shared_task
def billing_integrity_watch_task():
    """Reconcile materialized wallet balances against the immutable ledger."""
    output = io.StringIO()
    try:
        call_command("billing_integrity_check", stdout=output)
    except CommandError as exc:
        bucket = timezone.now().strftime("%Y-%m-%d-%H")
        _notify_platform_admins(
            dedupe_key=f"billing-integrity:{bucket}",
            title="Нарушена целостность пользовательского баланса",
            body=(
                "Кошелёк, резервы или ledger не сходятся. Автоматическая проверка "
                "остановилась с ошибкой; до выяснения причины не выполняйте ручные корректировки."
            ),
            action_url="/admin-console/finance",
            level=Notification.Level.WARNING,
        )
        raise RuntimeError(f"billing_integrity_check failed: {exc}") from exc
    return output.getvalue().strip()


@shared_task
def support_sla_watch_task():
    max_hours = max(1, int(os.getenv("SUPPORT_MAX_UNANSWERED_HOURS", "48")))
    now = timezone.now()
    warn_before = now - timedelta(hours=max(1, max_hours // 2))
    overdue_before = now - timedelta(hours=max_hours)
    open_requests = SupportRequest.objects.filter(
        status__in=[SupportRequest.Status.OPEN, SupportRequest.Status.IN_PROGRESS],
        admin_reply="",
        created_at__lt=warn_before,
    ).only("id", "subject", "created_at")
    admins = list(
        User.objects.filter(
            role=User.Role.PLATFORM_ADMIN,
            status=User.Status.ACTIVE,
        ).only("id")
    )
    warned = 0
    overdue = 0
    for request in open_requests.iterator():
        is_overdue = request.created_at < overdue_before
        level = Notification.Level.WARNING
        title = "Просрочено обращение поддержки" if is_overdue else "Обращение приближается к SLA"
        body = (
            f"Обращение «{request.subject}» не получило ответа более {max_hours} ч."
            if is_overdue
            else f"Обращение «{request.subject}» ожидает ответа и приближается к SLA {max_hours} ч."
        )
        stage = "overdue" if is_overdue else "warning"
        for admin in admins:
            _item, created = Notification.objects.get_or_create(
                user=admin,
                dedupe_key=f"support-sla:{request.id}:{stage}",
                defaults={
                    "title": title,
                    "body": body,
                    "level": level,
                    "action_url": "/admin-console/support",
                },
            )
            if created:
                if is_overdue:
                    overdue += 1
                else:
                    warned += 1
    return {"warnings_created": warned, "overdue_created": overdue}
