from __future__ import annotations

import os
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import Notification, User
from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import model_client_ready
from apps.procurement.models import ProviderSpendReservation

from .models import Generation


CHAT_HEALTH_LOCK = "chat:service-health-watch:lock"
ALERT_PREFIX = "chat:service-health-watch:alert:"
MIN_FAILURE_SAMPLE = max(3, int(os.getenv("CHAT_HEALTH_MIN_FAILURE_SAMPLE", "5")))
FAILURE_RATE_THRESHOLD = max(
    0.05,
    min(float(os.getenv("CHAT_HEALTH_FAILURE_RATE_THRESHOLD", "0.35")), 1.0),
)


def _notify_admins(*, key: str, title: str, body: str, level=Notification.Level.WARNING):
    bucket = timezone.now().strftime("%Y-%m-%d-%H-%M")[:-1]
    created = 0
    for admin in User.objects.filter(
        role=User.Role.PLATFORM_ADMIN,
        status=User.Status.ACTIVE,
    ).only("id").iterator():
        _row, was_created = Notification.objects.get_or_create(
            user=admin,
            dedupe_key=f"chat-health:{key}:{bucket}",
            defaults={
                "title": title,
                "body": body,
                "level": level,
                "action_url": "/admin-console/chat-diagnostics",
            },
        )
        created += int(was_created)
    return created


def _alert_once(*, key: str, title: str, body: str, critical: bool = False):
    # Avoid one notification per minute during a long external outage. The current
    # snapshot is still returned every run and remains visible in diagnostics.
    if not cache.add(f"{ALERT_PREFIX}{key}", "1", timeout=10 * 60):
        return 0
    return _notify_admins(
        key=key,
        title=title,
        body=body,
        level=Notification.Level.ERROR if critical else Notification.Level.WARNING,
    )


def chat_service_snapshot() -> dict:
    now = timezone.now()
    recent_since = now - timedelta(minutes=15)
    stale_cutoff = now - timedelta(seconds=settings.OPERATION_STALE_TIMEOUT_SECONDS)

    enabled_models = list(
        AIModel.objects.filter(enabled=True)
        .select_related("provider", "current_version")
        .order_by("provider__priority", "slug")
    )
    ready_models = []
    readiness_errors = 0
    for model in enabled_models:
        try:
            if model_client_ready(model):
                ready_models.append(model.slug)
        except Exception:
            readiness_errors += 1

    recent = Generation.objects.filter(created_at__gte=recent_since)
    recent_total = recent.count()
    recent_failed = recent.filter(state=Generation.State.FAILED).count()
    failure_rate = recent_failed / recent_total if recent_total else 0.0

    stale_generations = Generation.objects.filter(
        state__in=[Generation.State.QUEUED, Generation.State.RUNNING],
        created_at__lt=stale_cutoff,
    ).count()
    stale_provider_reservations = ProviderSpendReservation.objects.filter(
        state=ProviderSpendReservation.State.ACTIVE,
        source_key__startswith="chat:",
        created_at__lt=stale_cutoff,
    ).count()

    return {
        "generated_at": now.isoformat(),
        "enabled_models": len(enabled_models),
        "ready_models": len(ready_models),
        "ready_model_slugs": ready_models,
        "readiness_errors": readiness_errors,
        "requests_15m": recent_total,
        "failed_15m": recent_failed,
        "failure_rate_15m": round(failure_rate, 4),
        "stale_generations": stale_generations,
        "stale_provider_reservations": stale_provider_reservations,
    }


@shared_task(max_retries=0)
def chat_service_health_watch_task():
    """Detect customer-visible chat degradation before users have to report it."""
    if not cache.add(CHAT_HEALTH_LOCK, "1", timeout=55):
        return {"status": "skipped", "reason": "already_running"}
    try:
        snapshot = chat_service_snapshot()
        alerts = 0
        if snapshot["ready_models"] == 0:
            alerts += _alert_once(
                key="no-routable-models",
                title="Чат: нет доступных моделей",
                body=(
                    "Клиентский readiness не нашёл ни одной routable AI-модели. "
                    "Проверьте ключи, закупочный баланс, цены, quarantine и routing pools."
                ),
                critical=True,
            )
        if (
            snapshot["requests_15m"] >= MIN_FAILURE_SAMPLE
            and snapshot["failure_rate_15m"] >= FAILURE_RATE_THRESHOLD
        ):
            alerts += _alert_once(
                key="failure-rate",
                title="Чат: повышенный процент ошибок",
                body=(
                    f"За 15 минут завершились ошибкой {snapshot['failed_15m']} из "
                    f"{snapshot['requests_15m']} запросов "
                    f"({snapshot['failure_rate_15m'] * 100:.1f}%). Откройте диагностику чата."
                ),
            )
        if snapshot["stale_generations"] or snapshot["stale_provider_reservations"]:
            alerts += _alert_once(
                key="stale-runtime",
                title="Чат: обнаружены зависшие операции",
                body=(
                    f"Stale generations: {snapshot['stale_generations']}; "
                    f"stale provider reservations: {snapshot['stale_provider_reservations']}. "
                    "Автовосстановление уже запущено; проверьте диагностику, если состояние повторяется."
                ),
            )
        if snapshot["readiness_errors"]:
            alerts += _alert_once(
                key="readiness-errors",
                title="Чат: ошибки проверки доступности моделей",
                body=(
                    f"Readiness-проверка завершилась с внутренними ошибками для "
                    f"{snapshot['readiness_errors']} моделей."
                ),
            )
        return {"status": "ok", "alerts_created": alerts, **snapshot}
    finally:
        cache.delete(CHAT_HEALTH_LOCK)
