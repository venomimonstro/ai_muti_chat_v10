import os
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError
from apps.ai_registry.token_estimator import estimate_message_tokens
from apps.billing.pricing import active_price, quote, require_margin
from apps.billing.services import release

from .accounting import agent_provider_checkpoint_pending, release_agent_provider_spend
from .dev_changes import developer_output_contract, parse_change_proposal
from .dev_context import build_repository_context
from .dev_execution import apply_approved_changes, enrich_changes_with_snapshot
from .dev_model_execution import DevStageCanceled, execute_with_model_fallback
from .dev_plan import director_output_contract, plan_for_run, plan_rows_for_ui
from .dev_prompt import build_dev_messages
from .limits import effective_remaining_budget
from .models import AgentApproval, AgentRun, AgentStepRun
from .runtime import _model_for


OUTPUT_TOKENS = int(os.getenv("DEV_AGENT_OUTPUT_TOKENS", "4000"))
MAX_PREVIOUS_CHARS = int(os.getenv("DEV_AGENT_PREVIOUS_CHARS", "50000"))
MAX_STAGE_TEXT_CHARS = int(os.getenv("DEV_AGENT_STAGE_TEXT_CHARS", "16000"))


def _task_instructions(task):
    if not task:
        return ""
    dependencies = ", ".join(task.get("depends_on") or []) or "нет"
    acceptance = str(task.get("acceptance") or "").strip() or "выполнить задачу проверяемо"
    return (
        "\n\nТекущая задача Director DAG:\n"
        f"ID: {task.get('id')}\n"
        f"Название: {task.get('title')}\n"
        f"Зависимости: {dependencies}\n"
        f"Критерий приёмки: {acceptance}\n"
        "Работай только в рамках этой задачи и общей цели запуска."
    )


def _messages(run, agent, role, repository_context, previous, task=None):
    trusted_contract = ""
    if role == "Engineering Director":
        trusted_contract += "\n\n" + director_output_contract()
    if role == "Development":
        trusted_contract += "\n\n" + developer_output_contract()
    trusted_contract += _task_instructions(task)
    return build_dev_messages(
        run=run,
        agent=agent,
        role=role,
        repository_context=repository_context,
        previous=previous,
        trusted_contract=trusted_contract,
        max_previous_chars=MAX_PREVIOUS_CHARS,
    )


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


def _defer_settlement_recovery(run, step, *, code):
    run.state = AgentRun.State.REVIEWING
    run.error_code = "agent_settlement_pending"
    run.error_message = (
        f"Provider delivery подтверждён, финансовое закрытие Dev stage прервано ({code}). "
        "Reconciliation продолжится без повторного вызова модели."
    )[:4000]
    run.finished_at = None
    run.save(
        update_fields=[
            "state",
            "error_code",
            "error_message",
            "finished_at",
            "updated_at",
        ]
    )
    step.public_log = (
        "Ответ провайдера получен. Финансовое закрытие Dev stage "
        "восстанавливается автоматически; повторный вызов модели не выполняется."
    )
    step.save(update_fields=["public_log"])
    return run


def _completed_outputs(run):
    rows = run.steps.filter(state=AgentStepRun.State.COMPLETED).order_by("sequence", "created_at")
    result = []
    for step in rows:
        text = str((step.output_payload or {}).get("text") or step.public_log or "").strip()
        if text:
            result.append({"role": step.title, "text": text[:MAX_STAGE_TEXT_CHARS]})
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


def _run_llm_stage(*, run, agent, role, repository_context, previous, sequence, total, budget, task=None):
    if _is_canceled(run):
        return None, total, run
    title = role
    node_id = f"team-step-{sequence}"
    if task:
        title = f"{role} · {task.get('title')}"[:240]
        node_id = f"dev-task-{str(task.get('id') or sequence)[:80]}"
    step = AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=sequence,
        node_id=node_id,
        title=title,
        action_type="github_read+llm" if sequence == 1 else "llm",
        state=AgentStepRun.State.RUNNING,
        public_log=f"{title}: выполняется.",
        started_at=timezone.now(),
    )
    try:
        primary_model = _model_for(agent)
        messages = _messages(run, agent, role, repository_context, previous, task=task)
        output_tokens = min(max(400, OUTPUT_TOKENS), primary_model.max_output_tokens)
        estimated_input = max(32, estimate_message_tokens(messages) + 16)
        primary_price = active_price(primary_model.slug)
        primary_preflight = require_margin(
            quote(
                primary_price,
                estimated_input,
                output_tokens,
                provider_slug=primary_model.provider.slug,
                model_slug=primary_model.slug,
                operation_type="agent",
            )
        )
        team_remaining = max(Decimal("0"), budget - total)
        agent_remaining, agent_budget = effective_remaining_budget(agent, run=run)
        effective_remaining = min(team_remaining, agent_remaining)
        if primary_preflight.user_charge_rub > effective_remaining:
            if _is_canceled(run):
                _mark_step_canceled(step)
                return None, total, run
            step.state = AgentStepRun.State.SKIPPED
            if agent_remaining <= team_remaining:
                step.public_log = (
                    f"{title}: шаг не запущен из-за лимита сотрудника. "
                    f"Расчётный максимум {primary_preflight.user_charge_rub} ₽, доступно {agent_remaining} ₽. "
                    f"За запуск {agent_budget['run_spend']}/{agent_budget['run_limit']} ₽; "
                    f"сегодня {agent_budget['day_spend']}/{agent_budget['day_limit']} ₽; "
                    f"за месяц {agent_budget['month_spend']}/{agent_budget['month_limit']} ₽."
                )
                error_code = "agent_period_budget_exceeded"
            else:
                step.public_log = (
                    f"Шаг не запущен: расчётный максимум {primary_preflight.user_charge_rub} ₽ превышает "
                    f"остаток бюджета Dev Team {team_remaining} ₽."
                )
                error_code = "team_budget_exceeded"
            step.finished_at = timezone.now()
            step.save(update_fields=["state", "public_log", "finished_at"])
            run.state = AgentRun.State.BUDGET_EXCEEDED
            run.error_code = error_code
            run.error_message = step.public_log
            run.step_count = sequence
            run.finished_at = timezone.now()
            run.save(update_fields=["state", "error_code", "error_message", "step_count", "finished_at", "updated_at"])
            return None, total, run

        if _is_canceled(run):
            _mark_step_canceled(step)
            return None, total, run

        generation = execute_with_model_fallback(
            run=run,
            sequence=sequence,
            primary_model=primary_model,
            messages=messages,
            estimated_input_tokens=estimated_input,
            requested_output_tokens=output_tokens,
            remaining_budget_rub=effective_remaining,
            is_canceled=lambda: _is_canceled(run),
            step=step,
        )
        result = generation.result
        selected_model = generation.model
        actual = generation.actual_rub
        total += actual

        if _is_canceled(run):
            return None, total, _finish_stage_after_cancel(run, step, result, actual, total)

        step.state = AgentStepRun.State.COMPLETED
        step.output_payload = {
            "text": result.text,
            "model": selected_model.slug,
            "primary_model": primary_model.slug,
            "provider_request_id": result.provider_request_id,
            "provider_attempts": generation.provider_attempts,
            "model_attempts": generation.model_attempts,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "director_task": task or None,
            "budget_before": {
                "team_remaining_rub": str(team_remaining),
                "agent_remaining_rub": str(agent_remaining),
            },
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
    except DevStageCanceled:
        _mark_step_canceled(step, "Запуск отменён во время выбора/переключения модели; активные резервы освобождены.")
        return None, total, run
    except ProviderError as exc:
        attempts = getattr(exc, "model_attempts", None)
        if attempts:
            payload = dict(step.output_payload or {})
            payload["model_attempts"] = attempts
            step.output_payload = payload
            step.save(update_fields=["output_payload"])
        if agent_provider_checkpoint_pending(step):
            return None, total, _defer_settlement_recovery(
                run,
                step,
                code=exc.code,
            )
        return None, total, _fail(run, step, exc.code, str(exc))
    except Exception as exc:
        return None, total, _fail(run, step, "team_runtime_failed", str(exc))


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
        "pending_changes": [
            {
                "path": item["path"],
                "operation": item["operation"],
                "reason": item.get("reason", ""),
                "risk_flags": item.get("risk_flags") or [],
            }
            for item in enriched
        ],
        "director_plan": run.plan,
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
        public_log="Проверяем подтверждённые изменения в Dev Workspace.",
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
    sandbox = execution.get("sandbox") or {}
    checks = sandbox.get("checks") or []
    check_text = ", ".join(
        f"{item.get('command')}={'PASS' if item.get('ok') else 'FAIL'}" for item in checks
    ) or sandbox.get("command", "validation")
    write_step.public_log = (
        f"Workspace: {check_text}. Рабочая ветка: {execution.get('branch')}. "
        f"Изменено файлов: {len(applied)}."
    )
    write_step.finished_at = timezone.now()
    write_step.save(update_fields=["state", "output_payload", "public_log", "finished_at"])
    run.step_count = sequence
    run.tool_call_count += 1 + len(applied)
    run.input_payload = {
        **(run.input_payload or {}),
        "phase": "reviewing_changes",
        "working_branch": execution.get("branch"),
        "workspace_id": execution.get("workspace_id"),
    }
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
        previous.append({"role": role, "text": text[:MAX_STAGE_TEXT_CHARS]})

    if _is_canceled(run):
        return run
    final_text = previous[-1]["text"] if previous else ""
    run.output_payload = {
        "text": final_text,
        "repository": branch_context["repository"],
        "working_branch": execution.get("branch"),
        "workspace_id": execution.get("workspace_id"),
        "repository_files": [item["path"] for item in branch_context["files"]],
        "applied_changes": applied,
        "sandbox": sandbox,
        "director_plan": run.plan,
        "stages": previous,
    }
    run.input_payload = {**(run.input_payload or {}), "phase": "completed"}
    run.state = AgentRun.State.COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["output_payload", "input_payload", "state", "finished_at", "updated_at"])
    return run


def _merge_development_changes(developer_steps):
    merged = []
    seen = set()
    for step in developer_steps:
        changes = parse_change_proposal((step.output_payload or {}).get("text") or "")
        for change in changes:
            path = change["path"]
            if path in seen:
                raise ValidationError(
                    f"Несколько Development-задач предложили изменить один файл {path}. "
                    "Director должен декомпозировать работу без конфликтующих записей."
                )
            seen.add(path)
            merged.append(change)
    return merged


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

    run.tool_call_count = int(repository_context.get("tool_calls") or 0)
    run.state = AgentRun.State.RUNNING
    run.plan = [
        {"id": "director", "title": "Engineering Director · планирование", "role": "Engineering Director", "state": "running"}
    ]
    run.save(update_fields=["plan", "tool_call_count", "state", "updated_at"])

    previous = []
    director_member = _member_by_role(members, "Engineering Director")
    director = director_member.agent if director_member else run.team.director
    sequence = _next_sequence(run)
    director_text, total, terminal = _run_llm_stage(
        run=run,
        agent=director,
        role="Engineering Director",
        repository_context=repository_context,
        previous=previous,
        sequence=sequence,
        total=total,
        budget=budget,
    )
    if terminal:
        return terminal
    previous.append({"role": "Engineering Director", "text": director_text[:MAX_STAGE_TEXT_CHARS]})

    try:
        director_plan = plan_for_run(director_text)
    except ValidationError as exc:
        director_step = run.steps.filter(sequence=sequence).first()
        return _fail(run, director_step, "invalid_director_plan", str(exc))
    run.plan = plan_rows_for_ui(director_plan)
    run.input_payload = {
        **(run.input_payload or {}),
        "phase": "executing_director_plan",
        "director_plan_version": director_plan.get("version", 2),
        "director_plan_summary": director_plan.get("summary", ""),
    }
    run.save(update_fields=["plan", "input_payload", "updated_at"])

    developer_steps = []
    for task in director_plan.get("tasks") or []:
        if _is_canceled(run):
            return run
        role = task.get("role")
        if role in {"QA & Security", "Final Review"}:
            continue
        member = _member_by_role(members, role)
        if not member:
            return _fail(run, None, "director_role_unavailable", f"Director назначил задачу роли {role}, которой нет в Dev Team")
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
            task=task,
        )
        if terminal:
            return terminal
        previous.append({"role": f"{role} · {task.get('title')}", "text": text[:MAX_STAGE_TEXT_CHARS]})
        if role == "Development":
            developer_step = run.steps.filter(sequence=sequence).first()
            if developer_step:
                developer_steps.append(developer_step)

    if _is_canceled(run):
        return run
    if developer_steps:
        try:
            changes = _merge_development_changes(developer_steps)
        except ValidationError as exc:
            return _fail(run, developer_steps[-1], "invalid_change_proposal", str(exc))
        if changes:
            try:
                return _await_write_approval(run, developer_steps[-1], changes, repository_context)
            except ValidationError as exc:
                return _fail(run, developer_steps[-1], "unsafe_change_proposal", str(exc))

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
        previous.append({"role": role, "text": text[:MAX_STAGE_TEXT_CHARS]})

    if _is_canceled(run):
        return run
    final_text = previous[-1]["text"] if previous else ""
    run.output_payload = {
        "text": final_text,
        "repository": repository_context["repository"],
        "repository_files": [item["path"] for item in repository_context["files"]],
        "director_plan": run.plan,
        "stages": previous,
    }
    run.input_payload = {**(run.input_payload or {}), "phase": "completed"}
    run.state = AgentRun.State.COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["output_payload", "input_payload", "state", "finished_at", "updated_at"])
    return run
