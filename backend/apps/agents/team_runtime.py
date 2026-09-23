from decimal import ROUND_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.token_estimator import estimate_message_tokens
from apps.billing.pricing import active_price, quote, require_margin
from apps.billing.services import release, reserve, settle
from apps.procurement.models import ProviderFundingAccount
from apps.procurement.services import release_provider_spend, reserve_provider_spend, settle_provider_spend

from .dev_context import build_repository_context
from .models import AgentRun, AgentStepRun
from .runtime import _model_for


OUTPUT_TOKENS = 1200
NATIVE_STEP = Decimal("0.000001")
MAX_PREVIOUS_CHARS = 18000


def _provider_reserve(model, provider_cost_rub, fx_snapshot, source_key):
    configured = ProviderFundingAccount.objects.filter(provider=model.provider, active=True, is_default=True).exists()
    if not configured:
        if getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False):
            raise ValidationError(f"Для провайдера {model.provider.slug} не настроен закупочный аккаунт")
        return None
    if not fx_snapshot or fx_snapshot.rate <= 0:
        if getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False):
            raise ValidationError("Не удалось определить FX для закупочного расхода")
        return None
    native = (Decimal(provider_cost_rub) / Decimal(fx_snapshot.rate)).quantize(NATIVE_STEP, rounding=ROUND_UP)
    if native <= 0:
        return None
    try:
        return reserve_provider_spend(provider=model.provider, amount_native=native, source_key=source_key)
    except ValidationError:
        if getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False):
            raise
        return None


def _provider_settle(reservation, model, result, actual_quote, source_id, customer_charge):
    if not reservation:
        return None
    fx = actual_quote.fx_snapshot
    if not fx or fx.rate <= 0:
        release_provider_spend(reservation.id)
        return None
    native = (Decimal(actual_quote.provider_cost_rub) / Decimal(fx.rate)).quantize(NATIVE_STEP, rounding=ROUND_UP)
    return settle_provider_spend(
        reservation_id=reservation.id,
        actual_native=native,
        nominal_cost_rub=actual_quote.provider_cost_rub,
        customer_charge_rub=customer_charge,
        source_type="agent",
        source_id=source_id,
        model_slug=model.slug,
        provider_request_id=result.provider_request_id,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


def _messages(run, agent, role, repository_context, previous):
    prior = ""
    if previous:
        rendered = "\n\n".join(f"[{item['role']}]\n{item['text']}" for item in previous)
        prior = "\n\nРезультаты предыдущих участников команды:\n" + rendered[-MAX_PREVIOUS_CHARS:]
    repo = repository_context["rendered"] if repository_context else "Repository context unavailable"
    system = (
        "Ты участник автономной AI-команды разработки. Не выдавай предположения за выполненные действия. "
        "Не раскрывай скрытые рассуждения. Давай проверяемые выводы, конкретные файлы и следующий шаг.\n"
        f"Твоя роль: {role}.\n"
        f"Имя агента: {agent.name}.\n"
        f"Постоянная цель роли: {agent.objective}.\n"
        f"Разрешённые инструменты: {agent.tool_policy}.\n\n"
        f"GitHub repository context:\n{repo}{prior}"
    )
    user = (
        f"Общая задача команды:\n{run.objective}\n\n"
        "Выполни свою часть работы. Если требуется изменение файлов или запуск команд, подготовь точный план/патч/команды, "
        "но не утверждай, что они уже применены, пока внешний инструмент реально не выполнил действие."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _fail(run, step, code, message, customer_reservation=None, provider_reservation=None):
    if customer_reservation:
        try:
            release(customer_reservation.id)
        except Exception:
            pass
    if provider_reservation:
        try:
            release_provider_spend(provider_reservation.id)
        except Exception:
            pass
    now = timezone.now()
    if step:
        step.state = AgentStepRun.State.FAILED
        step.public_log = f"Ошибка: {message}"[:12000]
        step.finished_at = now
        step.save(update_fields=["state", "public_log", "finished_at"])
    run.state = AgentRun.State.FAILED
    run.error_code = str(code)[:120]
    run.error_message = str(message)[:4000]
    run.finished_at = now
    run.save(update_fields=["state", "error_code", "error_message", "finished_at", "updated_at"])
    return run


def execute_team_run(run_id):
    with transaction.atomic():
        run = (
            AgentRun.objects.select_for_update()
            .select_related("owner", "team__director", "project")
            .get(pk=run_id)
        )
        if run.state != AgentRun.State.QUEUED:
            return run
        if not run.team_id or not run.project_id:
            run.state = AgentRun.State.FAILED
            run.error_code = "dev_team_project_missing"
            run.error_message = "Dev Team должна быть привязана к проекту"
            run.finished_at = timezone.now()
            run.save(update_fields=["state", "error_code", "error_message", "finished_at", "updated_at"])
            return run
        run.state = AgentRun.State.PLANNING
        run.started_at = run.started_at or timezone.now()
        run.save(update_fields=["state", "started_at", "updated_at"])

    try:
        repository_context = build_repository_context(run.project)
    except Exception as exc:
        return _fail(run, None, "repository_context_failed", str(exc))

    members = list(run.team.members.filter(enabled=True).select_related("agent").order_by("priority", "role"))
    if not members:
        return _fail(run, None, "team_empty", "В команде нет активных участников")

    stages = [(member.agent, member.role) for member in members]
    if len(stages) > 1:
        stages.append((run.team.director, "Final Review"))
    run.plan = [
        {"id": f"team-step-{index}", "title": role, "state": "pending", "agent": str(agent.id)}
        for index, (agent, role) in enumerate(stages, start=1)
    ]
    run.tool_call_count = int(repository_context.get("tool_calls") or 0)
    run.state = AgentRun.State.RUNNING
    run.save(update_fields=["plan", "tool_call_count", "state", "updated_at"])

    previous = []
    total = Decimal("0")
    budget = Decimal(str(run.team.max_cost_rub_per_run))

    for sequence, (agent, role) in enumerate(stages, start=1):
        run.refresh_from_db(fields=["state"])
        if run.state == AgentRun.State.CANCELED:
            return run
        step = AgentStepRun.objects.create(
            run=run,
            agent=agent,
            sequence=sequence,
            node_id=f"team-step-{sequence}",
            title=role,
            action_type="github_read+llm" if sequence == 1 else "llm",
            state=AgentStepRun.State.RUNNING,
            public_log=f"{role}: выполняется.",
            started_at=timezone.now(),
        )
        customer_reservation = None
        provider_reservation = None
        try:
            model = _model_for(agent)
            messages = _messages(run, agent, role, repository_context if sequence <= 2 else None, previous)
            output_tokens = min(OUTPUT_TOKENS, model.max_output_tokens)
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
            if total + preflight.user_charge_rub > budget:
                step.state = AgentStepRun.State.SKIPPED
                step.public_log = (
                    f"Шаг не запущен: расчётная стоимость превысила общий лимит команды {budget} ₽."
                )
                step.finished_at = timezone.now()
                step.save(update_fields=["state", "public_log", "finished_at"])
                run.state = AgentRun.State.BUDGET_EXCEEDED
                run.error_code = "team_budget_exceeded"
                run.error_message = step.public_log
                run.step_count = sequence
                run.finished_at = timezone.now()
                run.save(update_fields=["state", "error_code", "error_message", "step_count", "finished_at", "updated_at"])
                return run

            billing_key = f"agent-run:{run.id}:step:{sequence}"
            provider_key = f"agent:{run.id}:step:{sequence}"
            customer_reservation = reserve(run.owner, preflight.user_charge_rub, billing_key)
            provider_reservation = _provider_reserve(model, preflight.provider_cost_rub, preflight.fx_snapshot, provider_key)
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
            actual = min(actual_quote.user_charge_rub, customer_reservation.amount_rub)
            settle(customer_reservation.id, actual)
            customer_reservation = None
            _provider_settle(
                provider_reservation,
                model,
                result,
                actual_quote,
                f"{run.id}:step:{sequence}",
                actual,
            )
            provider_reservation = None
            total += actual
            previous.append({"role": role, "text": result.text[:9000]})
            step.state = AgentStepRun.State.COMPLETED
            step.output_payload = {
                "text": result.text,
                "model": model.slug,
                "provider_request_id": result.provider_request_id,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            }
            step.public_log = result.text[:12000]
            step.cost_rub = actual
            step.finished_at = timezone.now()
            step.save(update_fields=["state", "output_payload", "public_log", "cost_rub", "finished_at"])
            run.step_count = sequence
            run.handoff_count = max(0, sequence - 1)
            run.cost_actual_rub = total
            plan = list(run.plan or [])
            if sequence - 1 < len(plan):
                plan[sequence - 1] = {**plan[sequence - 1], "state": "completed"}
            run.plan = plan
            run.save(update_fields=["step_count", "handoff_count", "cost_actual_rub", "plan", "updated_at"])
        except ProviderError as exc:
            return _fail(run, step, exc.code, str(exc), customer_reservation, provider_reservation)
        except Exception as exc:
            return _fail(run, step, "team_runtime_failed", str(exc), customer_reservation, provider_reservation)

    final_text = previous[-1]["text"] if previous else ""
    run.output_payload = {
        "text": final_text,
        "repository": repository_context["repository"],
        "repository_files": [item["path"] for item in repository_context["files"]],
        "stages": previous,
    }
    run.state = AgentRun.State.COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["output_payload", "state", "finished_at", "updated_at"])
    return run
