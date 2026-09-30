from django.core.management.base import BaseCommand, CommandError

from apps.agents.models import AgentRun, AgentStepRun, AgentTeam
from apps.billing.models import BalanceReservation
from apps.github_integration.models import GitHubOperationLog
from apps.procurement.models import ProviderSpendReservation


class Command(BaseCommand):
    help = "Audit one completed Dev Studio journey as commercial E2E evidence"

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=True)

    def handle(self, *args, **options):
        run_id = str(options["run_id"]).strip()
        failures = []
        try:
            run = (
                AgentRun.objects.select_related("team", "project")
                .prefetch_related("steps", "approvals")
                .get(pk=run_id)
            )
        except AgentRun.DoesNotExist as exc:
            raise CommandError(f"Dev run not found: {run_id}") from exc

        if not run.team_id or run.team.kind != AgentTeam.Kind.DEVELOPMENT:
            failures.append("run is not a Dev Studio team run")
        if run.state != AgentRun.State.COMPLETED:
            failures.append(f"run state is {run.state}, expected completed")
        if run.error_code:
            failures.append(f"completed run still contains error_code={run.error_code}")

        output = run.output_payload or {}
        branch = str(output.get("working_branch") or (run.input_payload or {}).get("working_branch") or "")
        workspace_id = str(output.get("workspace_id") or (run.input_payload or {}).get("workspace_id") or "")
        sandbox = output.get("sandbox") or {}
        applied = output.get("applied_changes") or []
        director_plan = output.get("director_plan") or run.plan or []

        if not branch.startswith("ai-workspace/run-"):
            failures.append("isolated working branch evidence is missing")
        if not workspace_id:
            failures.append("workspace evidence is missing")
        if not sandbox or sandbox.get("ok") is not True:
            failures.append("sandbox PASS evidence is missing")
        if not sandbox.get("evidence_level"):
            failures.append("sandbox evidence_level is missing")
        sandbox_checks = sandbox.get("checks") or []
        if any(not item.get("ok") for item in sandbox_checks if isinstance(item, dict)):
            failures.append("sandbox contains a failed verification check")
        if not isinstance(applied, list) or not applied:
            failures.append("applied_changes evidence is missing")
        if not isinstance(director_plan, list) or not director_plan:
            failures.append("Director DAG evidence is missing")

        steps = list(run.steps.all())
        non_completed = [
            step for step in steps if step.state != AgentStepRun.State.COMPLETED
        ]
        if non_completed:
            summary = ", ".join(
                f"{step.node_id or step.sequence}:{step.state}" for step in non_completed[:8]
            )
            failures.append(f"non-completed steps remain in completed run: {summary}")

        completed_roles = [step.title for step in steps if step.state == AgentStepRun.State.COMPLETED]
        if not any("QA & Security" in title for title in completed_roles):
            failures.append("completed QA & Security stage is missing")
        if not any("Final Review" in title for title in completed_roles):
            failures.append("completed Final Review stage is missing")

        if isinstance(director_plan, list):
            completed_nodes = {
                step.node_id for step in steps if step.state == AgentStepRun.State.COMPLETED and step.node_id
            }
            for task in director_plan:
                if not isinstance(task, dict):
                    failures.append("Director DAG contains a non-object task")
                    continue
                role = str(task.get("role") or "")
                task_id = str(task.get("id") or "")
                if role in {"Architecture", "Development"}:
                    expected_node = f"dev-task-{task_id[:80]}"
                    if not task_id or expected_node not in completed_nodes:
                        failures.append(
                            f"Director DAG node was not completed: {task_id or '<missing-id>'}"
                        )

        approved_decision = run.approvals.filter(
            status="approved",
            action_payload__kind="github_changes",
        ).order_by("-decided_at", "-created_at").first()
        if approved_decision is None:
            failures.append("approved github_changes decision is missing")
        elif approved_decision.decided_by_id != run.owner_id:
            failures.append("github_changes approval was not decided by the run owner")

        github_logs = GitHubOperationLog.objects.filter(
            branch=branch,
            metadata__run_id=str(run.id),
            action__startswith="agent_",
        )
        github_success_count = github_logs.filter(success=True).count()
        github_failure_count = github_logs.filter(success=False).count()
        applied_count = len(applied) if isinstance(applied, list) else 0
        if not github_success_count:
            failures.append("successful GitHub write audit evidence is missing")
        elif github_success_count < applied_count:
            failures.append(
                f"GitHub write evidence is incomplete: {github_success_count}/{applied_count} files"
            )
        if github_failure_count:
            failures.append(f"failed GitHub write audit entries remain: {github_failure_count}")

        customer_active = BalanceReservation.objects.filter(
            wallet__user_id=run.owner_id,
            state=BalanceReservation.State.ACTIVE,
            idempotency_key__startswith=f"agent-run:{run.id}:step:",
        ).count()
        provider_active = ProviderSpendReservation.objects.filter(
            state=ProviderSpendReservation.State.ACTIVE,
            source_key__startswith=f"agent:{run.id}:step:",
        ).count()
        if customer_active:
            failures.append(f"active customer reservations remain: {customer_active}")
        if provider_active:
            failures.append(f"active provider reservations remain: {provider_active}")

        model_attempts = []
        for step in steps:
            attempts = (step.output_payload or {}).get("model_attempts") or []
            if not isinstance(attempts, list) or not attempts:
                continue
            model_attempts.extend(attempts)
            statuses = [
                str(item.get("status") or "")
                for item in attempts
                if isinstance(item, dict)
            ]
            if "settlement_interrupted" in statuses:
                failures.append(
                    f"step {step.node_id or step.sequence} contains interrupted settlement evidence"
                )
            if not statuses or statuses[-1] != "completed":
                failures.append(
                    f"step {step.node_id or step.sequence} has no completed final model attempt"
                )

        self.stdout.write("=== DEV STUDIO COMMERCIAL E2E AUDIT ===")
        self.stdout.write(
            f"run={run.id} state={run.state} branch={branch or '-'} workspace={workspace_id or '-'} "
            f"applied_files={applied_count} steps={len(steps)} cost_rub={run.cost_actual_rub} "
            f"model_attempts={len(model_attempts)} github_writes={github_success_count}"
        )
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))
        if failures:
            raise CommandError(f"Dev Studio commercial E2E audit failed: {len(failures)} blocker(s)")
        self.stdout.write(self.style.SUCCESS("DEV_STUDIO_COMMERCIAL_E2E_OK"))
