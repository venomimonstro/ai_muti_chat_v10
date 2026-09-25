import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.connections.models import AgentConnectionBinding, ExternalConnection
from apps.connections.wordpress import create_wordpress_post

from .models import AgentApproval, AgentRun, AgentStepRun


def _title(run, text):
    for line in str(text or "").splitlines():
        candidate = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if 4 <= len(candidate) <= 180:
            return candidate
    return str(run.objective or "Материал AI Workspace").strip()[:180]


def _text_before(run, sequence):
    steps = (
        run.steps.filter(state=AgentStepRun.State.COMPLETED, sequence__lt=sequence)
        .order_by("-sequence", "-created_at")
    )
    for step in steps:
        text = str((step.output_payload or {}).get("text") or step.public_log or "").strip()
        if text and step.action_type not in {"approval", "image"}:
            return text
    return str((run.output_payload or {}).get("text") or "").strip()


def _wordpress_connection(agent):
    rows = list(
        AgentConnectionBinding.objects.filter(
            agent=agent,
            purpose="publish",
            enabled=True,
            connection__enabled=True,
            connection__kind=ExternalConnection.Kind.WORDPRESS,
        )
        .select_related("connection")
        .order_by("created_at")[:2]
    )
    if not rows:
        raise ValidationError("Сначала подключите WordPress к агенту")
    if len(rows) > 1:
        raise ValidationError("К агенту подключено несколько WordPress. Оставьте одно активное подключение для автономной публикации")
    connection = rows[0].connection
    if connection.health_state != ExternalConnection.Health.HEALTHY:
        raise ValidationError("WordPress подключение не прошло проверку здоровья")
    return connection


def _ancestor_node_ids(agent, node_id):
    graph = agent.graph or {}
    edges = graph.get("edges") or [] if isinstance(graph, dict) else []
    reverse = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("from") or "").strip()
        target = str(edge.get("to") or "").strip()
        if source and target:
            reverse.setdefault(target, set()).add(source)

    ancestors = set()
    stack = list(reverse.get(str(node_id), set()))
    while stack:
        current = stack.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        stack.extend(reverse.get(current, set()))
    return ancestors


def _approved_for_publish(run, publish_step):
    ancestors = _ancestor_node_ids(run.agent, publish_step.node_id)
    if not ancestors:
        return False
    return AgentApproval.objects.filter(
        run=run,
        status=AgentApproval.Status.APPROVED,
        action_payload__kind="workflow_approval",
        action_payload__node_id__in=ancestors,
        step__sequence__lt=publish_step.sequence,
    ).exists()


def _can_publish(run, step):
    policy = str((run.agent.tool_policy or {}).get("publish") or "").strip().lower()
    if policy == "approval":
        if not _approved_for_publish(run, step):
            raise ValidationError("Публикация требует подтверждения пользователя в этой ветке workflow перед узлом publish")
        return True
    if policy in {"auto", "autonomous", "true"} and run.agent.autonomy == run.agent.Autonomy.AUTONOMOUS:
        return True
    raise ValidationError("Автономная публикация не разрешена политикой агента")


def _stable_publish_slug(run, step, node, title):
    configured = str(node.get("slug") or "").strip()
    base = slugify(configured or title, allow_unicode=True).strip("-") or "ai-workspace"
    node_slug = slugify(str(step.node_id or "publish"), allow_unicode=True).strip("-") or "publish"
    suffix = f"{str(run.id).split('-')[0]}-{node_slug[:32]}"
    max_base = max(1, 190 - len(suffix))
    return f"{base[:max_base].rstrip('-')}-{suffix}"[:200]


def _mark_failure(run, step, message):
    now = timezone.now()
    step.state = AgentStepRun.State.FAILED
    step.public_log = f"WordPress: {message}"[:12000]
    step.finished_at = now
    step.save(update_fields=["state", "public_log", "finished_at"])
    run.state = AgentRun.State.FAILED
    run.error_code = "wordpress_publish_failed"
    run.error_message = str(message)[:4000]
    run.finished_at = now
    run.save(update_fields=["state", "error_code", "error_message", "finished_at", "updated_at"])
    return run


@transaction.atomic
def finalize_graph_publish_nodes(run_id):
    run = (
        AgentRun.objects.select_for_update()
        .select_related("agent")
        .prefetch_related("steps", "approvals")
        .get(pk=run_id)
    )
    if run.state != AgentRun.State.COMPLETED or not run.agent_id:
        return run

    publish_steps = list(
        run.steps.filter(action_type="publish").order_by("sequence", "created_at")
    )
    if not publish_steps:
        return run

    output = dict(run.output_payload or {})
    existing = output.get("wordpress_publication")
    if isinstance(existing, dict) and existing.get("post_id"):
        return run

    for step in publish_steps:
        if step.state == AgentStepRun.State.COMPLETED and (step.output_payload or {}).get("wordpress_publication"):
            continue
        try:
            _can_publish(run, step)
            connection = _wordpress_connection(run.agent)
            text = _text_before(run, step.sequence)
            if not text:
                raise ValidationError("Перед публикацией нет готового текста")
            if len(text) > 500000:
                raise ValidationError("Материал слишком большой для автоматической публикации")

            node = next(
                (
                    item
                    for item in ((run.agent.graph or {}).get("nodes") or [])
                    if str(item.get("id") or "") == step.node_id
                ),
                {},
            )
            # Safe default: legacy/new publish nodes without an explicit status
            # must create a WordPress draft. Going live is always an explicit
            # visual-builder choice, additionally protected by agent policy.
            target_status = str(node.get("status") or "draft").strip().lower()
            if target_status not in {"draft", "publish"}:
                raise ValidationError("Узел publish поддерживает только draft или publish")

            title = str(node.get("post_title") or "").strip() or _title(run, text)
            stable_slug = _stable_publish_slug(run, step, node, title)
            result = create_wordpress_post(
                connection,
                title=title,
                content=text,
                status=target_status,
                slug=stable_slug,
            )
            publication = {
                **result,
                "connection_id": str(connection.id),
                "connection_name": connection.name,
            }
            step.state = AgentStepRun.State.COMPLETED
            step.output_payload = {"wordpress_publication": publication}
            step.public_log = (
                f"WordPress: {'найдена существующая запись' if result.get('existing') else ('опубликовано' if result.get('status') == 'publish' else 'сохранено как черновик')}. "
                f"Post ID: {result.get('post_id')}."
            )
            step.finished_at = timezone.now()
            step.save(update_fields=["state", "output_payload", "public_log", "finished_at"])
            output["wordpress_publication"] = publication
            run.output_payload = output
            run.tool_call_count += 1
            run.save(update_fields=["output_payload", "tool_call_count", "updated_at"])
        except ValidationError as exc:
            return _mark_failure(run, step, str(exc))
        except Exception as exc:
            return _mark_failure(run, step, f"внешний сервис недоступен: {exc}")
    return run
