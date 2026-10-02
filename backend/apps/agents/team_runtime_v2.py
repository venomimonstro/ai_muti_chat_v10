"""Dev Studio execution compatibility layer for Sprint 87.

The orchestration/approval implementation remains in team_runtime while the
paid LLM stage is upgraded here to accounting-safe cross-model failover.
Importing this module installs the v2 stage once per worker process; there is
no per-request monkeypatch and therefore no concurrency window.
"""

from decimal import Decimal

from django.utils import timezone

from apps.ai_registry.adapters import ProviderError
from apps.ai_registry.token_estimator import estimate_message_tokens

from . import team_runtime as legacy
from .dev_model_execution import DevStageCanceled, execute_with_model_fallback
from .models import AgentRun, AgentStepRun


def _mark_budget_exceeded(run, step, *, title, estimated_message, sequence, code):
    step.state = AgentStepRun.State.SKIPPED
    step.public_log = f"{title}: {estimated_message}"[:12000]
    step.finished_at = timezone.now()
    step.save(update_fields=["state", "public_log", "finished_at"])
    run.state = AgentRun.State.BUDGET_EXCEEDED
    run.error_code = code
    run.error_message = step.public_log
    run.step_count = sequence
    run.finished_at = timezone.now()
    run.save(
        update_fields=[
            "state",
            "error_code",
            "error_message",
            "step_count",
            "finished_at",
            "updated_at",
        ]
    )
    return run


def _run_llm_stage_v2(*, run, agent, role, repository_context, previous, sequence, total, budget, task=None):
    if legacy._is_canceled(run):
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
        # Resolve through legacy module intentionally: existing safety tests and
        # operational monkeypatches keep the same seam after Sprint 87.
        primary_model = legacy._model_for(agent)
        messages = legacy._messages(run, agent, role, repository_context, previous, task=task)
        requested_output = min(max(400, legacy.OUTPUT_TOKENS), int(primary_model.max_output_tokens))
        estimated_input = max(32, estimate_message_tokens(messages) + 16)

        team_remaining = max(Decimal("0"), budget - total)
        agent_remaining, agent_budget = legacy.effective_remaining_budget(agent, run=run)
        effective_remaining = min(team_remaining, agent_remaining)
        if effective_remaining <= 0:
            code = "agent_period_budget_exceeded" if agent_remaining <= team_remaining else "team_budget_exceeded"
            message = (
                f"доступный бюджет исчерпан. Остаток команды {team_remaining} ₽, "
                f"остаток сотрудника {agent_remaining} ₽."
            )
            return None, total, _mark_budget_exceeded(
                run,
                step,
                title=title,
                estimated_message=message,
                sequence=sequence,
                code=code,
            )

        execution = execute_with_model_fallback(
            run=run,
            sequence=sequence,
            primary_model=primary_model,
            messages=messages,
            estimated_input_tokens=estimated_input,
            requested_output_tokens=requested_output,
            remaining_budget_rub=effective_remaining,
            is_canceled=lambda: legacy._is_canceled(run),
            step=step,
        )
        result = execution.result
        actual = Decimal(execution.actual_rub)
        total += actual

        if legacy._is_canceled(run):
            return None, total, legacy._finish_stage_after_cancel(run, step, result, actual, total)

        step.state = AgentStepRun.State.COMPLETED
        step.output_payload = {
            "text": result.text,
            "model": execution.model.slug,
            "provider": execution.model.provider.slug,
            "primary_model": primary_model.slug,
            "provider_request_id": result.provider_request_id,
            "provider_attempts": execution.provider_attempts,
            "model_attempts": execution.model_attempts,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "director_task": task or None,
            "budget_before": {
                "team_remaining_rub": str(team_remaining),
                "agent_remaining_rub": str(agent_remaining),
                "agent_run_spend_rub": str(agent_budget.get("run_spend", "0")),
            },
        }
        step.public_log = result.text[:12000]
        step.cost_rub = actual
        step.finished_at = timezone.now()
        step.save(
            update_fields=["state", "output_payload", "public_log", "cost_rub", "finished_at"]
        )
        run.step_count = max(run.step_count, sequence)
        run.handoff_count = max(run.handoff_count, max(0, sequence - 1))
        run.cost_actual_rub = total
        run.save(update_fields=["step_count", "handoff_count", "cost_actual_rub", "updated_at"])
        return result.text, total, None
    except DevStageCanceled:
        legacy._mark_step_canceled(step, "Запуск отменён; резервы текущей модели освобождены.")
        return None, total, run
    except ProviderError as exc:
        attempts = getattr(exc, "model_attempts", None)
        settlement_checkpoint = dict(
            getattr(exc, "settlement_checkpoint", {}) or {}
        )
        if attempts or settlement_checkpoint:
            payload = dict(step.output_payload or {})
            if attempts:
                payload["model_attempts"] = attempts
            if settlement_checkpoint:
                payload["_provider_settlement"] = settlement_checkpoint
            step.output_payload = payload
            step.save(update_fields=["output_payload"])
        if legacy.agent_provider_checkpoint_pending(step):
            return None, total, legacy._defer_settlement_recovery(
                run,
                step,
                code=exc.code,
            )
        return None, total, legacy._fail(run, step, exc.code, str(exc))
    except Exception as exc:
        if legacy.agent_provider_checkpoint_pending(step):
            return None, total, legacy._defer_settlement_recovery(
                run,
                step,
                code="team_runtime_failed",
            )
        return None, total, legacy._fail(run, step, "team_runtime_failed", str(exc))


# Install the upgraded stage once at worker/module import time. execute_team_run
# itself stays the battle-tested orchestration implementation.
legacy._run_llm_stage = _run_llm_stage_v2
execute_team_run = legacy.execute_team_run
