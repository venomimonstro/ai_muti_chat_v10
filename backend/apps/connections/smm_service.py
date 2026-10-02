from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.agents.models import Agent, AgentRun
from apps.agents.run_views import create_single_agent_run

from .models import AgentConnectionBinding, ExternalConnection
from .smm_media import prepare_item_media
from .smm_models import SMMContentItem, SMMContentPlan, SMMPublicationAttempt
from .vk import publish_wall_post


SMM_AGENT_NAME = "SMM-специалист VK"
ACTIVE_GENERATION_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}


def ensure_smm_agent(*, owner, connection):
    if connection.owner_id != owner.id or connection.kind != ExternalConnection.Kind.VK:
        raise ValidationError("VK-подключение недоступно")
    agent = Agent.objects.filter(
        owner=owner,
        name=SMM_AGENT_NAME,
        status__in=[Agent.Status.ACTIVE, Agent.Status.DRAFT, Agent.Status.PAUSED],
    ).first()
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
            tool_policy={
                "web": True,
                "browser": True,
                "vk": True,
                "image_generation": True,
                "stock_images": True,
                # External write is deliberately not a generic agent capability.
                # SMM publish goes only through the dedicated approval/scheduler path.
                "publish": False,
            },
            max_cost_rub_per_run=20,
            max_cost_rub_per_day=100,
            max_cost_rub_per_month=1500,
            max_steps=30,
            max_tool_calls=30,
        )
    else:
        changed = []
        if agent.status != Agent.Status.ACTIVE:
            agent.status = Agent.Status.ACTIVE
            changed.append("status")
        policy = dict(agent.tool_policy or {})
        required_policy = {
            "web": True,
            "browser": True,
            "vk": True,
            "image_generation": True,
            "stock_images": True,
            "publish": False,
        }
        if any(policy.get(key) != value for key, value in required_policy.items()):
            policy.update(required_policy)
            agent.tool_policy = policy
            changed.append("tool_policy")
        if changed:
            agent.save(update_fields=[*changed, "updated_at"])
    binding, _created = AgentConnectionBinding.objects.get_or_create(
        agent=agent,
        connection=connection,
        purpose="publish",
        defaults={"enabled": True},
    )
    if not binding.enabled:
        binding.enabled = True
        binding.save(update_fields=["enabled"])
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
        if run.state in ACTIVE_GENERATION_STATES:
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


def _schedule_from_value(value, plan):
    raw = str(value or "").strip()
    scheduled = None
    if raw:
        try:
            scheduled = datetime.fromisoformat(raw)
            if timezone.is_naive(scheduled):
                scheduled = timezone.make_aware(scheduled)
        except ValueError:
            scheduled = None
    if scheduled and plan.period_start <= timezone.localtime(scheduled).date() <= plan.period_end:
        return scheduled
    return None


@transaction.atomic
def sync_generated_plan(plan: SMMContentPlan):
    # generation_run is nullable. PostgreSQL cannot lock the nullable side of the
    # OUTER JOIN produced by select_related(), so explicitly lock only the plan row.
    plan = (
        SMMContentPlan.objects.select_for_update(of=("self",))
        .select_related("generation_run")
        .get(pk=plan.pk)
    )
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
    prepared = []
    seen_days = set()
    for index, raw in enumerate(items[:60]):
        if not isinstance(raw, dict):
            continue
        scheduled_at = _schedule_from_value(raw.get("scheduled_at"), plan)
        if scheduled_at:
            day = timezone.localtime(scheduled_at).date().isoformat()
            if day in seen_days:
                scheduled_at = None
            else:
                seen_days.add(day)
        content = str(raw.get("content") or "").strip()
        title = str(raw.get("title") or raw.get("topic") or f"Публикация {index + 1}").strip()[:220]
        if not content:
            continue
        hashtags = raw.get("hashtags") if isinstance(raw.get("hashtags"), list) else []
        prepared.append(
            SMMContentItem(
                plan=plan,
                title=title or f"Публикация {index + 1}",
                topic=str(raw.get("topic") or "")[:240],
                objective=str(raw.get("objective") or "")[:240],
                content=content,
                cta=str(raw.get("cta") or "")[:300],
                hashtags=[str(tag)[:80] for tag in hashtags[:20] if str(tag).strip()],
                status=SMMContentItem.Status.DRAFT,
                scheduled_at=scheduled_at,
                media_prompt=str(raw.get("media_prompt") or "")[:2000],
                sort_order=(index + 1) * 10,
            )
        )
    if not prepared:
        plan.generation_error = "Генерация завершилась, но валидные публикации не найдены"
        plan.save(update_fields=["generation_error", "updated_at"])
        return {"state": run.state, "created": 0}
    SMMContentItem.objects.bulk_create(prepared)
    plan.status = SMMContentPlan.Status.ACTIVE
    plan.generation_error = ""
    plan.save(update_fields=["status", "generation_error", "updated_at"])
    return {"state": run.state, "created": len(prepared)}


def _publication_message(item: SMMContentItem):
    parts = [item.content.strip()]
    if item.cta.strip() and item.cta.strip() not in parts[0]:
        parts.append(item.cta.strip())
    tags = " ".join(str(tag).strip() for tag in (item.hashtags or []) if str(tag).strip())
    if tags:
        parts.append(tags)
    return "\n\n".join(part for part in parts if part)


def _publication_guid(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _claim_publication(item_id, key):
    with transaction.atomic():
        item = (
            SMMContentItem.objects.select_for_update()
            .select_related("plan__connection", "plan__owner")
            .get(pk=item_id)
        )
        existing = SMMPublicationAttempt.objects.select_for_update().filter(idempotency_key=key).first()
        if existing is not None and existing.item_id != item.id:
            raise ValidationError("Idempotency-Key уже используется другой публикацией")
        if item.external_post_id:
            if existing is None:
                existing = SMMPublicationAttempt.objects.create(
                    item=item,
                    idempotency_key=key,
                    state=SMMPublicationAttempt.State.SKIPPED,
                    external_post_id=item.external_post_id,
                    error_code="already_published",
                    error_message="Публикация уже существует",
                    finished_at=timezone.now(),
                )
            return item, existing, False
        if existing is not None:
            if existing.state in {SMMPublicationAttempt.State.COMPLETED, SMMPublicationAttempt.State.SKIPPED}:
                return item, existing, False
            if existing.state == SMMPublicationAttempt.State.STARTED and existing.started_at >= timezone.now() - timedelta(minutes=5):
                return item, existing, False
            existing.state = SMMPublicationAttempt.State.STARTED
            existing.external_post_id = ""
            existing.error_code = ""
            existing.error_message = ""
            existing.finished_at = None
            existing.save(update_fields=["state", "external_post_id", "error_code", "error_message", "finished_at"])
            attempt = existing
        else:
            try:
                with transaction.atomic():
                    attempt = SMMPublicationAttempt.objects.create(item=item, idempotency_key=key)
            except IntegrityError:
                attempt = SMMPublicationAttempt.objects.select_for_update().get(idempotency_key=key)
                if attempt.item_id != item.id:
                    raise ValidationError("Idempotency-Key уже используется другой публикацией")
                return item, attempt, False
        if item.status not in {
            SMMContentItem.Status.APPROVED,
            SMMContentItem.Status.SCHEDULED,
            SMMContentItem.Status.FAILED,
            SMMContentItem.Status.PUBLISHING,
        }:
            raise ValidationError("Пост должен быть одобрен или запланирован перед публикацией")
        connection = item.plan.connection
        if connection.health_state != ExternalConnection.Health.HEALTHY or not connection.enabled:
            raise ValidationError("VK-подключение недоступно")
        if not str((connection.metadata or {}).get("selected_group_id") or "").strip():
            raise ValidationError("Выберите сообщество VK в разделе Интеграции")
        item.status = SMMContentItem.Status.PUBLISHING
        item.publish_error = ""
        item.save(update_fields=["status", "publish_error", "updated_at"])
        return item, attempt, True


def publish_item(item: SMMContentItem, *, idempotency_key: str):
    key = str(idempotency_key or "").strip()
    if not key or len(key) > 180:
        raise ValidationError("Корректный Idempotency-Key обязателен")
    item, attempt, claimed = _claim_publication(item.id, key)
    if not claimed:
        return attempt
    connection = item.plan.connection
    group_id = str((connection.metadata or {}).get("selected_group_id") or "").strip()
    try:
        if not item.vk_attachment and item.media_source in {
            SMMContentItem.MediaSource.GENERATED,
            SMMContentItem.MediaSource.STOCK,
        }:
            item.vk_attachment = prepare_item_media(item)
            item.save(update_fields=["vk_attachment", "updated_at"])
        external_id = publish_wall_post(
            connection,
            group_id=group_id,
            message=_publication_message(item),
            attachments=item.vk_attachment,
            request_guid=_publication_guid(key),
        )
    except Exception as exc:
        with transaction.atomic():
            locked_attempt = SMMPublicationAttempt.objects.select_for_update().get(pk=attempt.pk)
            locked_item = SMMContentItem.objects.select_for_update().get(pk=item.pk)
            locked_attempt.state = SMMPublicationAttempt.State.FAILED
            locked_attempt.error_code = "vk_publish_failed"
            locked_attempt.error_message = str(exc)[:500]
            locked_attempt.finished_at = timezone.now()
            locked_attempt.save(update_fields=["state", "error_code", "error_message", "finished_at"])
            locked_item.status = SMMContentItem.Status.FAILED
            locked_item.publish_error = str(exc)[:500]
            locked_item.save(update_fields=["status", "publish_error", "updated_at"])
        raise
    now = timezone.now()
    with transaction.atomic():
        locked_attempt = SMMPublicationAttempt.objects.select_for_update().get(pk=attempt.pk)
        locked_item = SMMContentItem.objects.select_for_update().get(pk=item.pk)
        locked_attempt.state = SMMPublicationAttempt.State.COMPLETED
        locked_attempt.external_post_id = external_id
        locked_attempt.error_code = ""
        locked_attempt.error_message = ""
        locked_attempt.finished_at = now
        locked_attempt.save(update_fields=["state", "external_post_id", "error_code", "error_message", "finished_at"])
        locked_item.status = SMMContentItem.Status.PUBLISHED
        locked_item.external_post_id = external_id
        locked_item.published_at = now
        locked_item.publish_error = ""
        locked_item.save(update_fields=["status", "external_post_id", "published_at", "publish_error", "updated_at"])
    return locked_attempt


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
        .select_related("plan__connection", "plan__owner")
        .order_by("scheduled_at")[: max(1, min(int(limit), 200))]
    )
