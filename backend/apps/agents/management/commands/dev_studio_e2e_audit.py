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
        if not isinstance(applied, list) or not applied:
            failures.append("applied_changes evidence is missing")
        if not isinstance(director_plan, list) or not director_plan:
            failures.append("Director DAG evidence is missing")

        completed_roles = list(
            run.steps.filter(state=AgentStepRun.State.COMPLETED).values_list("title", flat=True)
        )
        if not any("QA & Security" in title for title in completed_roles):
            failures.append("completed QA & Security stage is missing")
        if not any("Final Review" in title for title in completed_roles):
            failures.append("completed Final Review stage is missing")

        approved = run.approvals.filter(
            status="approved",
            action_payload__kind="github_changes",
        ).exists()
        if not approved:
            failures.append("approved github_changes decision is missing")

        github_success = GitHubOperationLog.objects.filter(
            success=True,
            branch=branch,
            metadata__run_id=str(run.id),
            action__startswith="agent_",
        ).exists()
        if not github_success:
            failures.append("successful GitHub write audit evidence is missing")

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
        for step in run.steps.all():
            attempts = (step.output_payload or {}).get("model_attempts") or []
            if isinstance(attempts, list):
                model_attempts.extend(attempts)

        self.stdout.write("=== DEV STUDIO COMMERCIAL E2E AUDIT ===")
        self.stdout.write(
            f"run={run.id} state={run.state} branch={branch or '-'} workspace={workspace_id or '-'} "
            f"applied_files={len(applied) if isinstance(applied, list) else 0} "
            f"steps={run.steps.count()} cost_rub={run.cost_actual_rub} model_attempts={len(model_attempts)}"
        )
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))
        if failures:
            raise CommandError(f"Dev Studio commercial E2E audit failed: {len(failures)} blocker(s)")
        self.stdout.write(self.style.SUCCESS("DEV_STUDIO_COMMERCIAL_E2E_OK"))
