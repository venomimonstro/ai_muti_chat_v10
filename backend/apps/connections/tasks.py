from celery import shared_task
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import Notification

from .models import ExternalConnection
from .wordpress import check_wordpress


def _error_text(exc):
    if isinstance(exc, ValidationError):
        if hasattr(exc, "messages") and exc.messages:
            return " ".join(str(item) for item in exc.messages)[:240]
        return str(exc)[:240]
    return "Проверка подключения завершилась внутренней ошибкой"[:240]


def _notify(connection, *, recovered=False):
    bucket = timezone.now().strftime("%Y-%m-%d-%H")
    if recovered:
        title = "Подключение снова работает"
        body = f"{connection.name}: автоматическая проверка внешнего сервиса снова проходит успешно."
        level = Notification.Level.SUCCESS
        state = "recovered"
    else:
        title = "Проблема с внешним подключением"
        body = (
            f"{connection.name}: автоматическая проверка не прошла. "
            "Автономные внешние действия заблокированы до восстановления подключения."
        )
        level = Notification.Level.WARNING
        state = "degraded"
    Notification.objects.get_or_create(
        user=connection.owner,
        dedupe_key=f"external-connection:{connection.id}:{state}:{bucket}",
        defaults={
            "title": title,
            "body": body,
            "level": level,
            "action_url": "/app/agents",
        },
    )


@shared_task(max_retries=0)
def check_external_connections(limit=100):
    """Periodically verify enabled outbound connections without exposing secrets.

    Network calls are intentionally made outside a DB transaction. The final
    update is conditional on the connection's updated_at snapshot so a user who
    edits credentials while the check is in flight cannot have the fresh state
    overwritten by a stale result.
    """
    ids = list(
        ExternalConnection.objects.filter(enabled=True)
        .order_by("last_checked_at", "created_at")
        .values_list("id", flat=True)[: max(1, min(int(limit), 500))]
    )
    checked = 0
    healthy = 0
    degraded = 0
    skipped_changed = 0

    for connection_id in ids:
        connection = ExternalConnection.objects.filter(pk=connection_id, enabled=True).first()
        if connection is None:
            continue
        snapshot_updated_at = connection.updated_at
        previous_state = connection.health_state
        checked += 1

        try:
            if connection.kind == ExternalConnection.Kind.WORDPRESS:
                metadata = check_wordpress(connection)
            else:
                raise ValidationError("Тип подключения не поддерживается")
            now = timezone.now()
            updated = ExternalConnection.objects.filter(
                pk=connection.id,
                enabled=True,
                updated_at=snapshot_updated_at,
            ).update(
                health_state=ExternalConnection.Health.HEALTHY,
                last_error="",
                last_checked_at=now,
                metadata={**(connection.metadata or {}), **metadata},
                updated_at=now,
            )
            if not updated:
                skipped_changed += 1
                continue
            connection.refresh_from_db()
            healthy += 1
            if previous_state == ExternalConnection.Health.DEGRADED:
                _notify(connection, recovered=True)
        except Exception as exc:
            now = timezone.now()
            error = _error_text(exc)
            updated = ExternalConnection.objects.filter(
                pk=connection.id,
                enabled=True,
                updated_at=snapshot_updated_at,
            ).update(
                health_state=ExternalConnection.Health.DEGRADED,
                last_error=error,
                last_checked_at=now,
                updated_at=now,
            )
            if not updated:
                skipped_changed += 1
                continue
            connection.refresh_from_db()
            degraded += 1
            if previous_state != ExternalConnection.Health.DEGRADED:
                _notify(connection, recovered=False)

    return {
        "checked": checked,
        "healthy": healthy,
        "degraded": degraded,
        "skipped_changed": skipped_changed,
    }
