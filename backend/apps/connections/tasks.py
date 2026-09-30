from celery import shared_task
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import Notification

from .models import ExternalConnection
from .smm_models import SMMContentPlan
from .smm_service import due_items, publish_item, sync_generated_plan
from .vk import check_vk
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
            "action_url": "/app/integrations",
        },
    )


def _connection_metadata(connection):
    if connection.kind == ExternalConnection.Kind.WORDPRESS:
        return check_wordpress(connection)
    if connection.kind == ExternalConnection.Kind.VK:
        profile = check_vk(connection)
        return {
            "user_id": profile.user_id,
            "display_name": profile.display_name,
            "groups": profile.groups,
        }
    raise ValidationError("Тип подключения не поддерживается")


@shared_task(max_retries=0)
def check_external_connections(limit=100):
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
            metadata = _connection_metadata(connection)
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


@shared_task(max_retries=0)
def sync_smm_generations(limit=100):
    plans = list(
        SMMContentPlan.objects.filter(generation_run__isnull=False, items__isnull=True)
        .select_related("generation_run")
        .distinct()
        .order_by("updated_at")[: max(1, min(int(limit), 500))]
    )
    created = 0
    failed = 0
    for plan in plans:
        try:
            result = sync_generated_plan(plan)
            created += int(result.get("created") or 0)
        except Exception as exc:
            failed += 1
            plan.generation_error = str(exc)[:500]
            plan.save(update_fields=["generation_error", "updated_at"])
    return {"checked": len(plans), "created": created, "failed": failed}


@shared_task(max_retries=0)
def publish_due_smm_posts(limit=50):
    published = 0
    failed = 0
    skipped = 0
    items = due_items(limit=limit)
    for item in items:
        key = f"scheduled:{item.id}:{item.scheduled_at.isoformat()}"
        try:
            attempt = publish_item(item, idempotency_key=key)
            if attempt.state == attempt.State.COMPLETED:
                published += 1
            else:
                skipped += 1
        except Exception:
            failed += 1
    return {
        "checked": len(items),
        "published": published,
        "failed": failed,
        "skipped": skipped,
    }
