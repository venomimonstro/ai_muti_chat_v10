from __future__ import annotations

import json
import re
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.agents.models import Agent, AgentRun
from apps.agents.run_views import create_single_agent_run

from .models import AgentConnectionBinding, ExternalConnection
from .smm_models import SMMContentItem, SMMContentPlan, SMMPublicationAttempt
from .vk import publish_wall_post


SMM_AGENT_NAME = "SMM-специалист VK"


def ensure_smm_agent(*, owner, connection):
    if connection.owner_id != owner.id or connection.kind != ExternalConnection.Kind.VK:
        raise ValidationError("VK-подключение недоступно")
    agent = Agent.objects.filter(owner=owner, name=SMM_AGENT_NAME, status__in=[Agent.Status.ACTIVE, Agent.Status.DRAFT]).first()
    if agent is None:
        agent = Agent.objects.create(
            owner=owner,
            name=SMM_AGENT_NAME,
            role="SMM-специалист ВКонтакте",
            objective=(
                "Планировать и готовить контент для сообщества ВКонтакте: изучать бизнес и аудиторию, "
                "создавать контент-планы и тексты постов, предлагать визуалы и CTA."
            ),
            instructions=(
                "Не выдумывай факты о компании. Для актуальных сведений используй web, если он доступен. "
                "Публикация во внешнюю сеть выполняется только отдельным инструментом платформы. "
                "Для контент-плана возвращай строго JSON по схеме из задачи запуска."
            ),
            autonomy=Agent.Autonomy.SEMI_AUTONOMOUS,
            status=Agent.Status.ACTIVE,
            system_level="balanced",
            tool_policy={"web": True, "browser": True, "vk": True, "image_generation": True},
            max_cost_rub_per_run=20,
            max_cost_rub_per_day=100,
            max_cost_rub_per_month=1500,
            max_steps=30,
            max_tool_calls=30,
        )
    AgentConnectionBinding.objects.get_or_create(
        agent=agent,
        connection=connection,
        purpose="publish",
        defaults={"enabled": True},
    )
    return agent


def generation_objective(plan: SMMContentPlan, post_count: int) -> str:
    count = max(1, min(int(post_count), 60))
    return f"""Создай контент-план для ВКонтакте.
Период: {plan.period_start.isoformat()} — {plan.period_end.isoformat()}.
Количество публикаций: {count}.
Бизнес: {plan.business_context or 'информация не задана'}.
Цель: {plan.goal or 'регулярный полезный контент и обращения'}.
Аудитория: {plan.audience or 'не задана'}.
Tone of voice: {plan.tone or 'экспертный, понятный, живой'}.

Верни ТОЛЬКО валидный JSON-массив, без markdown и пояснений. Каждый объект:
{{"title":"...","topic":"...","objective":"...","content":"полный текст поста","cta":"...","hashtags":["#тег"],"scheduled_at":"YYYY-MM-DDTHH:MM:SS+03:00","media_prompt":"описание изображения без текста на картинке"}}
Требования: даты внутри периода; без повторов; факты не выдумывать; текст готов к публикации; CTA уместный, не навязчивый.
"""


def start_plan_generation(plan: SMMContentPlan, *, post_count: int = 12):
    if plan.generation_run_id:
        run = plan.generation_run
        if run.state in {
            AgentRun.State.QUEUED,
            AgentRun.State.PLANNING,
            AgentRun.State.RUNNING,
            AgentRun.State.WAITING_TOOL,
            AgentRun.State.WAITING_APPROVAL,
            AgentRun.State.REVIEWING,
        }:
            return run
    run = create_single_agent_run(
        owner=plan.owner,
        agent=plan.agent,
        objective=generation_objective(plan, post_count),
        input_payload={"trigger": "smm_content_plan", "plan_id": str(plan.id), "post_count": int(post_count)},
    )
    plan.generation_run = run
    plan.generation_error = ""
    plan.save(update_fields=["generation_run", "generation_error", "updated_at"])
    return run


def _extract_json_array(text: str):
    text = str(text or "").strip()
    try:
        payload = json.loads(text)
        if isinstance(payload, list):
            return payload
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if not match:
        raise ValidationError("Модель не вернула JSON-массив контент-плана")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValidationError("Не удалось разобрать JSON контент-плана") from exc
    if not isinstance(payload, list):
        raise ValidationError("Контент-план должен быть JSON-массивом")
    return payload


@transaction.atomic
def sync_generated_plan(plan: SMMContentPlan):
    plan = SMMContentPlan.objects.select_for_update().select_related("generation_run").get(pk=plan.pk)
    run = plan.generation_run
    if run is None:
        raise ValidationError("Генерация ещё не запускалась")
    if run.state in {AgentRun.State.FAILED, AgentRun.State.CANCELED, AgentRun.State.BUDGET_EXCEEDED}:
        plan.generation_error = (run.error_message or run.error_code or "Генерация не выполнена")[:500]
        plan.save(update_fields=["generation_error", "updated_at"])
        return {"state": run.state, "created": 0}
    if run.state != AgentRun.State.COMPLETED:
        return {"state": run.state, "created": 0}
    if plan.items.exists():
        return {"state": run.state, "created": 0}
    items = _extract_json_array((run.output_payload or {}).get("text") or "")
    created = 0
    for index, raw in enumerate(items[:60]):
        if not isinstance(raw, dict):
            continue
        scheduled_at = None
        value = str(raw.get("scheduled_at") or "").strip()
        if value:
            try:
                scheduled_at = timezone.datetime.fromisoformat(value)
                if timezone.is_naive(scheduled_at):
                    scheduled_at = timezone.make_aware(scheduled_at)
            except ValueError:
                scheduled_at = None
        if scheduled_at and not (plan.period_start <= scheduled_at.date() <= plan.period_end):
            scheduled_at = None
        content = str(raw.get("content") or "").strip()
        title = str(raw.get("title") or raw.get("topic") or f"Публикация {index + 1}").strip()[:220]
        if not content:
            continue
        hashtags = raw.get("hashtags") if isinstance(raw.get("hashtags"), list) else []
        SMMContentItem.objects.create(
            plan=plan,
            title=title,
            topic=str(raw.get("topic") or "")[:240],
            objective=str(raw.get("objective") or "")[:240],
            content=content,
            cta=str(raw.get("cta") or "")[:300],
            hashtags=[str(tag)[:80] for tag in hashtags[:20]],
            status=SMMContentItem.Status.DRAFT,
            scheduled_at=scheduled_at,
            media_prompt=str(raw.get("media_prompt") or "")[:2000],
            sort_order=(index + 1) * 10,
        )
        created += 1
    if not created:
        plan.generation_error = "Генерация завершилась, но валидные публикации не найдены"
    else:
        plan.status = SMMContentPlan.Status.ACTIVE
        plan.generation_error = ""
    plan.save(update_fields=["status", "generation_error", "updated_at"])
    return {"state": run.state, "created": created}


def _publication_message(item: SMMContentItem):
    parts = [item.content.strip()]
    if item.cta.strip() and item.cta.strip() not in parts[0]:
        parts.append(item.cta.strip())
    tags = " ".join(str(tag).strip() for tag in (item.hashtags or []) if str(tag).strip())
    if tags:
        parts.append(tags)
    return "\n\n".join(part for part in parts if part)


def publish_item(item: SMMContentItem, *, idempotency_key: str):
    key = str(idempotency_key or "").strip()[:180]
    if not key:
        raise ValidationError("Idempotency-Key обязателен")
    existing = SMMPublicationAttempt.objects.filter(idempotency_key=key).first()
    if existing:
        return existing
    plan = item.plan
    connection = plan.connection
    if connection.health_state != ExternalConnection.Health.HEALTHY or not connection.enabled:
        raise ValidationError("VK-подключение недоступно")
    group_id = str((connection.metadata or {}).get("selected_group_id") or "").strip()
    if not group_id:
        raise ValidationError("Выберите сообщество VK в разделе Интеграции")
    if item.external_post_id:
        return SMMPublicationAttempt.objects.create(
            item=item,
            idempotency_key=key,
            state=SMMPublicationAttempt.State.SKIPPED,
            external_post_id=item.external_post_id,
            error_code="already_published",
            error_message="Публикация уже существует",
            finished_at=timezone.now(),
        )
    attempt = SMMPublicationAttempt.objects.create(item=item, idempotency_key=key)
    item.status = SMMContentItem.Status.PUBLISHING
    item.publish_error = ""
    item.save(update_fields=["status", "publish_error", "updated_at"])
    try:
        external_id = publish_wall_post(
            connection,
            group_id=group_id,
            message=_publication_message(item),
            attachments=item.vk_attachment,
            idempotency_key=key,
        )
    except Exception as exc:
        attempt.state = SMMPublicationAttempt.State.FAILED
        attempt.error_code = "vk_publish_failed"
        attempt.error_message = str(exc)[:500]
        attempt.finished_at = timezone.now()
        attempt.save(update_fields=["state", "error_code", "error_message", "finished_at"])
        item.status = SMMContentItem.Status.FAILED
        item.publish_error = str(exc)[:500]
        item.save(update_fields=["status", "publish_error", "updated_at"])
        raise
    now = timezone.now()
    attempt.state = SMMPublicationAttempt.State.COMPLETED
    attempt.external_post_id = external_id
    attempt.finished_at = now
    attempt.save(update_fields=["state", "external_post_id", "finished_at"])
    item.status = SMMContentItem.Status.PUBLISHED
    item.external_post_id = external_id
    item.published_at = now
    item.publish_error = ""
    item.save(update_fields=["status", "external_post_id", "published_at", "publish_error", "updated_at"])
    return attempt


def due_items(limit=50):
    now = timezone.now()
    return list(
        SMMContentItem.objects.filter(
            status=SMMContentItem.Status.SCHEDULED,
            scheduled_at__lte=now,
            plan__auto_publish=True,
            plan__status=SMMContentPlan.Status.ACTIVE,
            plan__connection__enabled=True,
            plan__connection__health_state=ExternalConnection.Health.HEALTHY,
        )
        .select_related("plan__connection")
        .order_by("scheduled_at")[: max(1, min(int(limit), 200))]
    )
