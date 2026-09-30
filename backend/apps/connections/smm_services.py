from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.agents.limits import ensure_owner_run_capacity
from apps.agents.models import Agent, AgentRun
from apps.agents.readiness import require_agent_ready

from .models import AgentConnectionBinding, ExternalConnection
from .smm_models import SMMContentItem, SMMContentPlan, SMMPublicationAttempt
from .vk import publish_wall_post


SMM_AGENT_NAME = "SMM-специалист VK"


def ensure_smm_agent(*, user, connection, project=None):
    if connection.owner_id != user.id or connection.kind != ExternalConnection.Kind.VK:
        raise ValidationError("Нужно собственное VK-подключение")
    defaults = {
        "project": project,
        "role": "SMM-специалист ВКонтакте",
        "objective": (
            "Планировать и готовить полезный контент для сообщества ВКонтакте: исследовать актуальный контекст, "
            "собирать контент-планы, писать посты и готовить материалы к публикации."
        ),
        "instructions": (
            "Не выдумывай актуальные факты: для свежей информации используй web-инструмент. "
            "Не публикуй контент самостоятельно без явного разрешения SMM Studio. "
            "Учитывай цель бизнеса, аудиторию, тон бренда и разнообразие рубрик."
        ),
        "autonomy": Agent.Autonomy.SEMI_AUTONOMOUS,
        "status": Agent.Status.ACTIVE,
        "system_level": "balanced",
        "tool_policy": {
            "web": True,
            "files": True,
            "browser": {"enabled": True, "mode": "research_read_only"},
            "vk": {"read": True, "publish": False},
            "image_generation": True,
            "stock_search": True,
        },
        "memory_policy": {"enabled": True, "scope": "project_and_user"},
        "max_cost_rub_per_run": 20,
        "max_cost_rub_per_day": 200,
        "max_cost_rub_per_month": 3000,
        "max_steps": 30,
        "max_tool_calls": 30,
        "max_handoffs": 10,
        "max_retries_per_step": 3,
        "max_runtime_seconds": 900,
    }
    agent = Agent.objects.filter(owner=user, name=SMM_AGENT_NAME, status__in=[Agent.Status.ACTIVE, Agent.Status.PAUSED, Agent.Status.DRAFT]).first()
    if agent is None:
        agent = Agent(owner=user, name=SMM_AGENT_NAME, **defaults)
    else:
        for field, value in defaults.items():
            if field == "project" and project is None and agent.project_id:
                continue
            setattr(agent, field, value)
    agent.full_clean()
    agent.save()
    binding, _ = AgentConnectionBinding.objects.get_or_create(
        agent=agent,
        connection=connection,
        purpose="smm_publish",
        defaults={"enabled": True},
    )
    if not binding.enabled:
        binding.enabled = True
        binding.save(update_fields=["enabled"])
    return agent


def build_plan_objective(plan: SMMContentPlan) -> str:
    days = max(1, (plan.period_end - plan.period_start).days + 1)
    desired = min(30, max(5, round(days / 2)))
    return f"""Создай профессиональный контент-план ВКонтакте.
Бизнес: {plan.business_context or 'контекст не заполнен'}
Цель: {plan.goal or 'рост полезного охвата и заявок'}
Аудитория: {plan.audience or 'целевая аудитория бизнеса'}
Тон: {plan.tone or 'экспертный, понятный, живой'}
Период: {plan.period_start.isoformat()} — {plan.period_end.isoformat()}.
Нужно примерно {desired} публикаций. Проверь актуальные факты через web, если тема этого требует.
Сделай разнообразные рубрики: польза, доверие, кейсы/доказательства, продукт, вовлечение, ответы на возражения.

В КОНЦЕ ответа верни один валидный JSON-объект без комментариев после него:
{{"posts":[{{"date":"YYYY-MM-DD","time":"12:00","title":"...","topic":"...","objective":"...","text":"готовый пост","cta":"...","hashtags":["tag"],"image_prompt":"описание изображения"}}]}}
JSON должен содержать только публикации внутри указанного периода, без markdown-ограждения.
""".strip()


def create_plan_run(*, plan: SMMContentPlan):
    ensure_owner_run_capacity(plan.owner)
    require_agent_ready(plan.agent)
    active = AgentRun.objects.filter(
        owner=plan.owner,
        agent=plan.agent,
        state__in=[
            AgentRun.State.QUEUED,
            AgentRun.State.PLANNING,
            AgentRun.State.RUNNING,
            AgentRun.State.WAITING_TOOL,
            AgentRun.State.WAITING_APPROVAL,
            AgentRun.State.REVIEWING,
        ],
    ).first()
    if active is not None:
        raise ValidationError("SMM-специалист уже выполняет задачу")
    return AgentRun.objects.create(
        owner=plan.owner,
        agent=plan.agent,
        project=plan.project,
        objective=build_plan_objective(plan),
        input_payload={"kind": "smm_content_plan", "plan_id": str(plan.id)},
        state=AgentRun.State.QUEUED,
    )


def _extract_json_object(text: str) -> dict:
    text = str(text or "").strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("posts"), list):
            return value
    raise ValidationError("AI не вернул корректный структурированный контент-план")


def import_generated_plan(*, plan: SMMContentPlan, text: str) -> int:
    payload = _extract_json_object(text)
    rows = payload.get("posts") or []
    prepared = []
    for index, row in enumerate(rows[:40]):
        if not isinstance(row, dict):
            continue
        try:
            day = datetime.strptime(str(row.get("date") or ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < plan.period_start or day > plan.period_end:
            continue
        raw_time = str(row.get("time") or "12:00")[:5]
        try:
            clock = datetime.strptime(raw_time, "%H:%M").time()
        except ValueError:
            clock = time(hour=12)
        scheduled = timezone.make_aware(datetime.combine(day, clock), timezone.get_current_timezone())
        text_value = str(row.get("text") or "").strip()
        title = str(row.get("title") or row.get("topic") or f"Публикация {index + 1}").strip()[:220]
        if not title or not text_value:
            continue
        tags = row.get("hashtags") if isinstance(row.get("hashtags"), list) else []
        prepared.append(
            SMMContentItem(
                plan=plan,
                title=title,
                topic=str(row.get("topic") or "")[:240],
                objective=str(row.get("objective") or "")[:240],
                content=text_value,
                cta=str(row.get("cta") or "")[:300],
                hashtags=[str(tag).strip().lstrip("#")[:80] for tag in tags[:15] if str(tag).strip()],
                status=SMMContentItem.Status.DRAFT,
                scheduled_at=scheduled,
                media_source=SMMContentItem.MediaSource.NONE,
                media_prompt=str(row.get("image_prompt") or "")[:2000],
                sort_order=(index + 1) * 10,
            )
        )
    if not prepared:
        raise ValidationError("AI не сформировал ни одной валидной публикации")
    with transaction.atomic():
        plan = SMMContentPlan.objects.select_for_update().get(pk=plan.pk)
        plan.items.exclude(status=SMMContentItem.Status.PUBLISHED).delete()
        SMMContentItem.objects.bulk_create(prepared)
        plan.status = SMMContentPlan.Status.ACTIVE
        plan.save(update_fields=["status", "updated_at"])
    return len(prepared)


def _publication_guid(key: str) -> str:
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:32]


def publish_item(*, item_id, user, idempotency_key: str, allow_draft=False):
    key = str(idempotency_key or "").strip()
    if not key or len(key) > 180:
        raise ValidationError("Для публикации нужен корректный Idempotency-Key")
    with transaction.atomic():
        item = (
            SMMContentItem.objects.select_for_update()
            .select_related("plan__connection", "plan__owner")
            .get(pk=item_id, plan__owner=user)
        )
        existing = SMMPublicationAttempt.objects.filter(idempotency_key=key).first()
        if existing:
            if existing.item_id != item.id:
                raise ValidationError("Idempotency-Key уже используется другой публикацией")
            return item, existing
        if item.external_post_id:
            attempt = SMMPublicationAttempt.objects.create(
                item=item,
                idempotency_key=key,
                state=SMMPublicationAttempt.State.SKIPPED,
                external_post_id=item.external_post_id,
                finished_at=timezone.now(),
            )
            return item, attempt
        allowed = {SMMContentItem.Status.APPROVED, SMMContentItem.Status.SCHEDULED, SMMContentItem.Status.FAILED}
        if allow_draft:
            allowed.add(SMMContentItem.Status.DRAFT)
        if item.status not in allowed:
            raise ValidationError("Пост ещё не готов к публикации")
        connection = item.plan.connection
        if not connection.enabled or connection.health_state != ExternalConnection.Health.HEALTHY:
            raise ValidationError("VK-подключение сейчас недоступно")
        group_id = str((connection.metadata or {}).get("selected_group_id") or "").strip()
        if not group_id:
            raise ValidationError("Выберите сообщество VK в разделе Интеграции")
        attempt = SMMPublicationAttempt.objects.create(item=item, idempotency_key=key)
        item.status = SMMContentItem.Status.PUBLISHING
        item.publish_error = ""
        item.save(update_fields=["status", "publish_error", "updated_at"])
        message = item.content.strip()
        if item.cta.strip():
            message = f"{message}\n\n{item.cta.strip()}"
        tags = " ".join(f"#{tag}" for tag in (item.hashtags or []) if str(tag).strip())
        if tags:
            message = f"{message}\n\n{tags}"
        attachment = item.vk_attachment.strip()

    try:
        external_post_id = publish_wall_post(
            connection,
            group_id=group_id,
            message=message,
            attachments=attachment,
            request_guid=_publication_guid(key),
        )
    except Exception as exc:
        with transaction.atomic():
            item = SMMContentItem.objects.select_for_update().get(pk=item_id)
            attempt = SMMPublicationAttempt.objects.select_for_update().get(pk=attempt.pk)
            item.status = SMMContentItem.Status.FAILED
            item.publish_error = str(exc)[:500]
            item.save(update_fields=["status", "publish_error", "updated_at"])
            attempt.state = SMMPublicationAttempt.State.FAILED
            attempt.error_code = "vk_publish_failed"
            attempt.error_message = str(exc)[:500]
            attempt.finished_at = timezone.now()
            attempt.save(update_fields=["state", "error_code", "error_message", "finished_at"])
        raise

    with transaction.atomic():
        item = SMMContentItem.objects.select_for_update().get(pk=item_id)
        attempt = SMMPublicationAttempt.objects.select_for_update().get(pk=attempt.pk)
        item.status = SMMContentItem.Status.PUBLISHED
        item.external_post_id = external_post_id
        item.published_at = timezone.now()
        item.publish_error = ""
        item.save(update_fields=["status", "external_post_id", "published_at", "publish_error", "updated_at"])
        attempt.state = SMMPublicationAttempt.State.COMPLETED
        attempt.external_post_id = external_post_id
        attempt.finished_at = timezone.now()
        attempt.save(update_fields=["state", "external_post_id", "finished_at"])
    return item, attempt


def due_items(now=None):
    now = now or timezone.now()
    return SMMContentItem.objects.filter(
        status=SMMContentItem.Status.SCHEDULED,
        scheduled_at__isnull=False,
        scheduled_at__lte=now,
        plan__status=SMMContentPlan.Status.ACTIVE,
        plan__auto_publish=True,
    ).select_related("plan__owner", "plan__connection")
