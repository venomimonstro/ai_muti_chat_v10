from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.token_estimator import estimate_message_tokens
from apps.billing.pricing import active_price, quote, require_margin
from apps.billing.services import release, reserve, settle

from .accounting import (
    release_agent_provider_spend,
    reserve_agent_provider_spend,
    settle_agent_provider_spend,
)
from .dev_changes import developer_output_contract, parse_change_proposal
from .dev_context import build_repository_context
from .dev_execution import apply_approved_changes, enrich_changes_with_snapshot
from .models import AgentApproval, AgentRun, AgentStepRun
from .runtime import _model_for


OUTPUT_TOKENS = 1600
MAX_PREVIOUS_CHARS = 18000


def _messages(run, agent, role, repository_context, previous):
    prior = ""
    if previous:
        rendered = "\n\n".join(f"[{item['role']}]\n{item['text']}" for item in previous)
        prior = "\n\nРезультаты предыдущих участников команды:\n" + rendered[-MAX_PREVIOUS_CHARS:]
    repo = repository_context["rendered"] if repository_context else "Repository context unavailable"
    extra = ""
    if role == "Development":
        extra = "\n\n" + developer_output_contract()
    system = (
        "Ты участник автономной AI-команды разработки. Не выдавай предположения за выполненные действия. "
        "Не раскрывай скрытые рассуждения. Давай проверяемые выводы, конкретные файлы и следующий шаг.\n"
        f"Твоя роль: {role}.\n"
        f"Имя агента: {agent.name}.\n"
        f"Постоянная цель роли: {agent.objective}.\n"
        f"Разрешённые инструменты: {agent.tool_policy}.\n\n"
        f"GitHub repository context:\n{repo}{prior}{extra}"
    )
    user = (
        f"Общая задача команды:\n{run.objective}\n\n"
        "Выполни свою часть работы. Если требуется изменение файлов или запуск команд, подготовь точный результат, "
        "но не утверждай, что внешнее действие выполнено, пока соответствующий инструмент реально не был вызван."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _is_canceled(run):
    run.refresh_from_db(fields=["state", "finished_at"])
    return run.state == AgentRun.State.CANCELED


def _release_customer(reservation):
    if not reservation:
        return
    try:
        release(reservation.id)
    except Exception:
        pass


def _release_provider(reservation):
    if not reservation:
        return
    try:
        release_agent_provider_spend(reservation)
    except Exception:
        pass


def _mark_step_canceled(step, message="Запуск отменён пользователем до следующего действия."):
    if step is None:
        return
    step.state = AgentStepRun.State.SKIPPED
    step.public_log = message
    step.finished_at = timezone.now()
    step.save(update_fields=["state", "public_log", "finished_at"])


def _fail(run, step, code, message, customer_reservation=None, provider_reservation=None):
    _release_customer(customer_reservation)
    _release_provider(provider_reservation)
    if _is_canceled(run):
        if step and step.state == AgentStepRun.State.RUNNING:
            _mark_step_canceled(step, "Запуск отменён пользователем. Незавершённые резервы освобождены.")
        return run
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


def _completed_outputs(run):
    rows = run.steps.filter(state=AgentStepRun.State.COMPLETED).order_by("sequence", "created_at")
    result = []
    for step in rows:
        text = str((step.output_payload or {}).get("text") or step.public_log or "").strip()
        if text:
            result.append({"role": step.title, "text": text[:9000]})
    return result


def _next_sequence(run):
    value = run.steps.aggregate(value=Max("sequence"))["value"] or 0
    return int(value) + 1


def _finish_stage_after_cancel(run, step, result, actual, total):
    now = timezone.now()
    step.state = AgentStepRun.State.COMPLETED
    step.output_payload = {
        "canceled_after_provider": True,
        "provider_request_id": result.provider_request_id,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }
    step.public_log = (
        "Пользователь остановил Dev Studio после отправки запроса модели. "
        "Фактически возникшая стоимость учтена; результат не передан следующему агенту."
    )
    step.cost_rub = actual
    step.finished_at = now
    step.save(update_fields=["state", "output_payload", "public_log", "cost_rub", "finished_at"])
    AgentRun.objects.filter(pk=run.pk, state=AgentRun.State.CANCELED).update(
        cost_actual_rub=total,
        finished_at=now,
        updated_at=now,
    )
    run.refresh_from_db()
    return run


def _run_llm_stage(*, run, agent, role, repository_context, previous, sequence, total, budget):
    if _is_canceled(run):
        return None, total, run
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
        messages = _messages(run, agent, role, repository_context, previous)
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
            if _is_canceled(run):
                _mark_step_canceled(step)
                return None, total, run
            step.state = AgentStepRun.State.SKIPPED
            step.public_log = f"Шаг не запущен: расчётная стоимость превысила общий лимит команды {budget} ₽."
            step.finished_at = timezone.now()
            step.save(update_fields=["state", "public_log", "finished_at"])
            run.state = AgentRun.State.BUDGET_EXCEEDED
            run.error_code = "team_budget_exceeded"
            run.error_message = step.public_log
            run.step_count = sequence
            run.finished_at = timezone.now()
            run.save(update_fields=["state", "error_code", "error_message", "step_count", "finished_at", "updated_at"])
            return None, total, run

        if _is_canceled(run):
            _mark_step_canceled(step)
            return None, total, run
        customer_reservation = reserve(run.owner, preflight.user_charge_rub, f"agent-run:{run.id}:step:{sequence}")
        provider_reservation = reserve_agent_provider_spend(
            model=model,
            provider_cost_rub=preflight.provider_cost_rub,
            fx_snapshot=preflight.fx_snapshot,
            source_key=f"agent:{run.id}:step:{sequence}",
        )
        if _is_canceled(run):
            _release_customer(customer_reservation)
            customer_reservation = None
            _release_provider(provider_reservation)
            provider_reservation = None
            _mark_step_canceled(step, "Запуск отменён до обращения к модели; резерв освобождён.")
            return None, total, run

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
        settle_agent_provider_spend(
            reservation=provider_reservation,
            model=model,
            result=result,
            actual_quote=actual_quote,
            source_id=f"{run.id}:step:{sequence}",
            customer_charge=actual,
        )
        provider_reservation = None
        settle(customer_reservation.id, actual)
        customer_reservation = None
        total += actual

        if _is_canceled(run):
            return None, total, _finish_stage_after_cancel(run, step, result, actual, total)

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
        run.step_count = max(run.step_count, sequence)
        run.handoff_count = max(run.handoff_count, max(0, sequence - 1))
        run.cost_actual_rub = total
        run.save(update_fields=["step_count", "handoff_count", "cost_actual_rub", "updated_at"])
        return result.text, total, None
    except ProviderError as exc:
        return None, total, _fail(run, step, exc.code, str(exc), customer_reservation, provider_reservation)
    except Exception as exc:
        return None, total, _fail(run, step, "team_runtime_failed", str(exc), customer_reservation, provider_reservation)


def _members(run):
    return list(run.team.members.filter(enabled=True).select_related("agent").order_by("priority", "role"))


def _member_by_role(members, role):
    return next((item for item in members if item.role == role), None)


def _await_write_approval(run, developer_step, changes, repository_context):
    if _is_canceled(run):
        return run
    enriched = enrich_changes_with_snapshot(changes, repository_context)
    if _is_canceled(run):
        return run
    approval = AgentApproval.objects.create(
        run=run,
        step=developer_step,
        requested_by_agent=developer_step.agent,
        title="Разрешить изменения в GitHub",
        description=(
            f"Dev Team предлагает изменить файлов: {len(enriched)}. После подтверждения система создаст отдельную "
            "рабочую ветку, проверит изменения в sandbox и только затем запишет их в GitHub. Default branch не меняется."
        ),
        action_payload={
            "kind": "github_changes",
            "repository": repository_context["repository"],
            "base_branch": repository_context["default_branch"],
            "changes": enriched,
        },
    )
    payload = dict(run.input_payload or {})
    payload["phase"] = "awaiting_github_approval"
    payload["approval_id"] = str(approval.id)
    run.input_payload = payload
    run.state = AgentRun.State.WAITING_APPROVAL
    run.output_payload = {
        "text": developer_step.public_log,
        "repository": repository_context["repository"],
        "repository_files": [item["path"] for item in repository_context["files"]],
        "pending_changes": [{"path": item["path"], "operation": item["operation"], "reason": item.get("reason", "")} for item in enriched],
    }
    run.save(update_fields=["input_payload", "state", "output_payload", "updated_at"])
    return run


def _continue_approved_write(run, approval, members, total, budget):
    if _is_canceled(run):
        return run
    changes = list((approval.action_payload or {}).get("changes") or [])
    developer_member = _member_by_role(members, "Development")
    developer = developer_member.agent if developer_member else run.team.director
    sequence = _next_sequence(run)
    write_step = AgentStepRun.objects.create(
        run=run,
        agent=developer,
        sequence=sequence,
        node_id="approved-github-write",
        title="Sandbox + GitHub write",
        action_type="sandbox+github_write",
        state=AgentStepRun.State.RUNNING,
        public_log="Проверяем подтверждённые изменения в sandbox.",
        started_at=timezone.now(),
    )
    if _is_canceled(run):
        _mark_step_canceled(write_step, "Запуск отменён до записи изменений в GitHub.")
        return run
    try:
        execution = apply_approved_changes(project=run.project, run_id=run.id, changes=changes)
    except Exception as exc:
        return _fail(run, write_step, "dev_write_failed", str(exc))
    write_step.state = AgentStepRun.State.COMPLETED
    write_step.output_payload = execution
    applied = execution.get("changes") or []
    write_step.public_log = (
        f"Sandbox: {execution.get('sandbox', {}).get('command', 'validation')}. "
        f"Рабочая ветка: {execution.get('branch')}. Записано файлов: {len(applied)}."
    )
    write_step.finished_at = timezone.now()
    write_step.save(update_fields=["state", "output_payload", "public_log", "finished_at"])
    run.step_count = sequence
    run.tool_call_count += 1 + len(applied)
    run.input_payload = {**(run.input_payload or {}), "phase": "reviewing_changes", "working_branch": execution.get("branch")}
    run.save(update_fields=["step_count", "tool_call_count", "input_payload", "updated_at"])

    if _is_canceled(run):
        return run
    try:
        branch_context = build_repository_context(run.project, ref=execution.get("branch"))
        run.tool_call_count += int(branch_context.get("tool_calls") or 0)
        run.save(update_fields=["tool_call_count", "updated_at"])
    except Exception as exc:
        return _fail(run, write_step, "branch_review_context_failed", str(exc))

    previous = _completed_outputs(run)
    qa_member = _member_by_role(members, "QA & Security")
    review_stages = []
    if qa_member:
        review_stages.append((qa_member.agent, "QA & Security"))
    review_stages.append((run.team.director, "Final Review"))
    for agent, role in review_stages:
        if _is_canceled(run):
            return run
        sequence = _next_sequence(run)
        text, total, terminal = _run_llm_stage(
            run=run,
            agent=agent,
            role=role,
            repository_context=branch_context,
            previous=previous,
            sequence=sequence,
            total=total,
            budget=budget,
        )
        if terminal:
            return terminal
        previous.append({"role": role, "text": text[:9000]})

    if _is_canceled(run):
        return run
    final_text = previous[-1]["text"] if previous else ""
    run.output_payload = {
        "text": final_text,
        "repository": branch_context["repository"],
        "working_branch": execution.get("branch"),
        "repository_files": [item["path"] for item in branch_context["files"]],
        "applied_changes": applied,
        "stages": previous,
    }
    run.input_payload = {**(run.input_payload or {}), "phase": "completed"}
    run.state = AgentRun.State.COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["output_payload", "input_payload", "state", "finished_at", "updated_at"])
    return run


def execute_team_run(run_id):
    with transaction.atomic():
        run = (
            AgentRun.objects.select_for_update()
            .select_related("owner", "team__director", "project")
            .prefetch_related("steps", "approvals")
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
        run.finished_at = None
        run.save(update_fields=["state", "started_at", "finished_at", "updated_at"])

    members = _members(run)
    if not members:
        return _fail(run, None, "team_empty", "В команде нет активных участников")
    total = Decimal(str(run.cost_actual_rub or 0))
    budget = Decimal(str(run.team.max_cost_rub_per_run))

    if _is_canceled(run):
        return run
    approved = run.approvals.filter(
        status=AgentApproval.Status.APPROVED,
        action_payload__kind="github_changes",
    ).order_by("-decided_at", "-created_at").first()
    phase = str((run.input_payload or {}).get("phase") or "")
    if approved and phase == "awaiting_github_approval":
        if _is_canceled(run):
            return run
        run.state = AgentRun.State.RUNNING
        run.save(update_fields=["state", "updated_at"])
        return _continue_approved_write(run, approved, members, total, budget)

    if run.steps.exists():
        return _fail(run, None, "invalid_team_resume", "Запуск команды нельзя безопасно повторить с текущей фазы")

    if _is_canceled(run):
        return run
    try:
        repository_context = build_repository_context(run.project)
    except Exception as exc:
        return _fail(run, None, "repository_context_failed", str(exc))

    if _is_canceled(run):
        return run
    run.plan = [
        {"id": "director", "title": "Engineering Director", "state": "pending"},
        {"id": "architecture", "title": "Architecture", "state": "pending"},
        {"id": "development", "title": "Development", "state": "pending"},
        {"id": "approval", "title": "Approval", "state": "conditional"},
        {"id": "sandbox-write", "title": "Sandbox + GitHub write", "state": "conditional"},
        {"id": "qa", "title": "QA & Security", "state": "pending"},
        {"id": "final", "title": "Final Review", "state": "pending"},
    ]
    run.tool_call_count = int(repository_context.get("tool_calls") or 0)
    run.state = AgentRun.State.RUNNING
    run.save(update_fields=["plan", "tool_call_count", "state", "updated_at"])

    previous = []
    primary_roles = ("Engineering Director", "Architecture", "Development")
    developer_step = None
    for role in primary_roles:
        if _is_canceled(run):
            return run
        member = _member_by_role(members, role)
        if not member:
            continue
        sequence = _next_sequence(run)
        text, total, terminal = _run_llm_stage(
            run=run,
            agent=member.agent,
            role=role,
            repository_context=repository_context,
            previous=previous,
            sequence=sequence,
            total=total,
            budget=budget,
        )
        if terminal:
            return terminal
        previous.append({"role": role, "text": text[:9000]})
        if role == "Development":
            developer_step = run.steps.filter(sequence=sequence).first()

    if _is_canceled(run):
        return run
    if developer_step:
        try:
            changes = parse_change_proposal((developer_step.output_payload or {}).get("text") or "")
        except ValidationError as exc:
            return _fail(run, developer_step, "invalid_change_proposal", str(exc))
        if changes:
            try:
                return _await_write_approval(run, developer_step, changes, repository_context)
            except ValidationError as exc:
                return _fail(run, developer_step, "unsafe_change_proposal", str(exc))

    qa_member = _member_by_role(members, "QA & Security")
    final_stages = []
    if qa_member:
        final_stages.append((qa_member.agent, "QA & Security"))
    final_stages.append((run.team.director, "Final Review"))
    for agent, role in final_stages:
        if _is_canceled(run):
            return run
        sequence = _next_sequence(run)
        text, total, terminal = _run_llm_stage(
            run=run,
            agent=agent,
            role=role,
            repository_context=repository_context,
            previous=previous,
            sequence=sequence,
            total=total,
            budget=budget,
        )
        if terminal:
            return terminal
        previous.append({"role": role, "text": text[:9000]})

    if _is_canceled(run):
        return run
    final_text = previous[-1]["text"] if previous else ""
    run.output_payload = {
        "text": final_text,
        "repository": repository_context["repository"],
        "repository_files": [item["path"] for item in repository_context["files"]],
        "stages": previous,
    }
    run.input_payload = {**(run.input_payload or {}), "phase": "completed"}
    run.state = AgentRun.State.COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["output_payload", "input_payload", "state", "finished_at", "updated_at"])
    return run
