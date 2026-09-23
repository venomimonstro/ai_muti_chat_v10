from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.models import AIModel, Provider, RoutingPolicyVersion
from apps.ai_registry.reliability import provider_available
from apps.ai_registry.token_estimator import estimate_message_tokens
from apps.billing.pricing import active_price, quote, require_margin
from apps.billing.services import release, reserve, settle

from .accounting import (
    release_agent_provider_spend,
    reserve_agent_provider_spend,
    settle_agent_provider_spend,
)
from .dev_context import build_repository_context
from .models import AgentRun, AgentStepRun


DEFAULT_MAX_OUTPUT_TOKENS = 1200


def _subject_agent(run: AgentRun):
    if run.agent_id:
        return run.agent
    if run.team_id:
        return run.team.director
    raise ValidationError("У запуска не назначен агент или команда")


def _model_for(agent):
    policy = RoutingPolicyVersion.objects.filter(active=True).first()
    pinned = ""
    if policy:
        pinned = str(((policy.thresholds or {}).get("tier_models") or {}).get(agent.system_level) or "").strip()
    queryset = AIModel.objects.filter(enabled=True).select_related("provider", "current_version")
    if pinned:
        model = queryset.filter(slug=pinned).first()
        if model and provider_available(model.provider):
            return model
    for model in queryset.order_by("provider__priority", "slug"):
        if model.provider.health_state == Provider.HealthState.HEALTHY and provider_available(model.provider):
            return model
    raise ValidationError("Для уровня агента нет доступной подключённой модели")


def _messages(run, agent, repository_context=None):
    team_context = ""
    if run.team_id:
        members = list(run.team.members.filter(enabled=True).select_related("agent").order_by("priority"))
        team_context = "\nКоманда:\n" + "\n".join(f"- {item.role}: {item.agent.name}" for item in members)
    repo = ""
    if repository_context:
        repo = (
            "\n\nКонтекст GitHub repository уже получен реальным read-only инструментом. "
            "Опирайся только на фактически переданные файлы и дерево, не выдумывай отсутствующий код.\n"
            + repository_context["rendered"]
        )
    system = (
        "Ты автономный AI-сотрудник внутри Agent Studio. "
        "Работай по роли, цели и ограничениям. Не заявляй, что выполнил внешнее действие, "
        "если инструмент для него не был реально вызван. Не раскрывай внутренние рассуждения; "
        "показывай краткий проверяемый план и полезный результат.\n"
        f"Роль: {agent.role or agent.name}\n"
        f"Постоянная цель: {agent.objective}\n"
        f"Инструкции: {agent.instructions or 'нет дополнительных инструкций'}\n"
        f"Автономность: {agent.autonomy}.\n"
        f"Разрешённые инструменты: {agent.tool_policy}.{team_context}{repo}"
    )
    user = (
        f"Задача запуска:\n{run.objective}\n\n"
        "Сначала дай короткий рабочий план, затем выполни ту часть задачи, которая возможна на текущем шаге. "
        "Если для продолжения требуется запись в GitHub, shell/sandbox, публикация или другой внешний инструмент, "
        "явно перечисли требуемое действие и не утверждай, что оно уже выполнено."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _mark_failure(run, step, *, code, message, reservation=None, provider_reservation=None):
    if reservation:
        try:
            release(reservation.id)
        except Exception:
            pass
    if provider_reservation:
        try:
            release_agent_provider_spend(provider_reservation)
        except Exception:
            pass
    now = timezone.now()
    step.state = AgentStepRun.State.FAILED
    step.public_log = f"Ошибка выполнения: {message}"[:4000]
    step.finished_at = now
    step.save(update_fields=["state", "public_log", "finished_at"])
    run.state = AgentRun.State.FAILED
    run.error_code = str(code)[:120]
    run.error_message = str(message)[:4000]
    run.finished_at = now
    run.save(update_fields=["state", "error_code", "error_message", "finished_at", "updated_at"])


def execute_run(run_id):
    with transaction.atomic():
        run = AgentRun.objects.select_for_update().select_related("owner", "agent", "team__director", "project").get(pk=run_id)
        if run.state != AgentRun.State.QUEUED:
            return run
        run.state = AgentRun.State.PLANNING
        run.started_at = timezone.now()
        run.step_count = 1
        run.save(update_fields=["state", "started_at", "step_count", "updated_at"])
        agent = _subject_agent(run)
        step = AgentStepRun.objects.create(
            run=run,
            agent=agent,
            sequence=1,
            node_id="plan-and-execute",
            title="Анализ и первый результат",
            action_type="github_read+llm" if run.team_id else "llm",
            state=AgentStepRun.State.RUNNING,
            started_at=timezone.now(),
            public_log="Агент анализирует задачу и готовит проверяемый результат.",
        )

    reservation = None
    provider_reservation = None
    repository_context = None
    try:
        run.refresh_from_db()
        if run.state == AgentRun.State.CANCELED:
            return run
        agent = _subject_agent(run)
        if run.team_id:
            if not run.project_id:
                raise ValidationError("Dev Team не привязана к проекту")
            repository_context = build_repository_context(run.project)
            run.tool_call_count = int(repository_context.get("tool_calls") or 0)
            step.public_log = (
                f"Repository {repository_context['repository']} прочитан в безопасном read-only режиме. "
                f"Получено файлов: {len(repository_context['files'])}. Engineering Director анализирует проект."
            )
            step.save(update_fields=["public_log"])
            run.save(update_fields=["tool_call_count", "updated_at"])
        model = _model_for(agent)
        messages = _messages(run, agent, repository_context)
        output_tokens = min(DEFAULT_MAX_OUTPUT_TOKENS, model.max_output_tokens)
        estimated_input = max(32, estimate_message_tokens(messages) + 16)
        price = active_price(model.slug)
        preflight = require_margin(
            quote(
                price,
                estimated_input,
                output_tokens,
                provider_slug=model.provider.slug,
                model_slug=model.slug,
                operation_type="agent",
            )
        )
        budget = Decimal(str(run.team.max_cost_rub_per_run if run.team_id else agent.max_cost_rub_per_run))
        if preflight.user_charge_rub > budget:
            now = timezone.now()
            step.state = AgentStepRun.State.FAILED
            step.public_log = f"Запуск остановлен до обращения к модели: расчётный максимум {preflight.user_charge_rub} ₽ превышает лимит {budget} ₽."
            step.finished_at = now
            step.save(update_fields=["state", "public_log", "finished_at"])
            run.state = AgentRun.State.BUDGET_EXCEEDED
            run.error_code = "agent_budget_exceeded"
            run.error_message = step.public_log
            run.finished_at = now
            run.save(update_fields=["state", "error_code", "error_message", "finished_at", "updated_at"])
            return run

        reservation = reserve(run.owner, preflight.user_charge_rub, f"agent-run:{run.id}")
        provider_reservation = reserve_agent_provider_spend(
            model=model,
            provider_cost_rub=preflight.provider_cost_rub,
            fx_snapshot=preflight.fx_snapshot,
            source_key=f"agent:{run.id}",
        )
        run.cost_reserved_rub = preflight.user_charge_rub
        run.state = AgentRun.State.RUNNING
        run.save(update_fields=["cost_reserved_rub", "state", "updated_at"])

        result = adapter_for(model).generate(
            model=model.upstream_model or model.slug,
            messages=messages,
            max_output_tokens=output_tokens,
        )
        actual_quote = require_margin(
            quote(
                price,
                max(1, result.input_tokens),
                max(1, result.output_tokens),
                provider_slug=model.provider.slug,
                model_slug=model.slug,
                operation_type="agent",
            )
        )
        actual = min(actual_quote.user_charge_rub, reservation.amount_rub)
        settle_agent_provider_spend(
            reservation=provider_reservation,
            model=model,
            result=result,
            actual_quote=actual_quote,
            source_id=run.id,
            customer_charge=actual,
        )
        provider_reservation = None
        settle(reservation.id, actual)
        reservation = None

        now = timezone.now()
        step.state = AgentStepRun.State.COMPLETED
        step.output_payload = {
            "text": result.text,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "provider_request_id": result.provider_request_id,
            "repository": repository_context["repository"] if repository_context else None,
        }
        step.public_log = result.text[:12000]
        step.cost_rub = actual
        step.finished_at = now
        step.save(update_fields=["state", "output_payload", "public_log", "cost_rub", "finished_at"])
        run.plan = [{"id": "plan-and-execute", "title": "Анализ и первый результат", "state": "completed", "agent": str(agent.id)}]
        run.output_payload = {
            "text": result.text,
            "repository": repository_context["repository"] if repository_context else None,
            "repository_files": [item["path"] for item in repository_context["files"]] if repository_context else [],
        }
        run.cost_actual_rub = actual
        run.state = AgentRun.State.COMPLETED
        run.finished_at = now
        run.save(update_fields=["plan", "output_payload", "cost_actual_rub", "state", "finished_at", "updated_at"])
        return run
    except ProviderError as exc:
        _mark_failure(run, step, code=exc.code, message=str(exc), reservation=reservation, provider_reservation=provider_reservation)
    except Exception as exc:
        _mark_failure(run, step, code="agent_runtime_failed", message=str(exc), reservation=reservation, provider_reservation=provider_reservation)
    return run
