from django.contrib.auth.hashers import identify_hasher
from django.core.management.base import BaseCommand, CommandError

from apps.agents.models import Agent, AgentApproval, AgentRun, AgentTeam, AgentTeamMember, AgentVersion
from apps.agents.schedule_models import AgentSchedule
from apps.agents.webhook_models import AgentWebhookDelivery, AgentWebhookTrigger


class Command(BaseCommand):
    help = "Fail closed on Agent/Dev Studio tenant-isolation and dangerous tool-policy violations"

    def handle(self, *args, **options):
        failures = []
        warnings = []

        self.stdout.write("=== AGENT SECURITY AUDIT ===")

        for agent in Agent.objects.select_related("owner", "project").iterator(chunk_size=200):
            if agent.project_id and agent.project.owner_id != agent.owner_id:
                failures.append(f"agent={agent.id}: project belongs to another tenant")
            policy = agent.tool_policy if isinstance(agent.tool_policy, dict) else {}
            shell = str(policy.get("shell") or "").strip().lower()
            if shell and shell != "sandbox":
                failures.append(f"agent={agent.id}: shell policy must be sandbox, got={shell}")
            merge = str(policy.get("merge") or "").strip().lower()
            if merge and merge not in {"approval", "false", "none"}:
                failures.append(f"agent={agent.id}: GitHub merge must require approval")
            if policy.get("write_code") and shell != "sandbox":
                failures.append(f"agent={agent.id}: write_code requires sandbox shell")

        for version in AgentVersion.objects.select_related("agent", "created_by").iterator(chunk_size=200):
            if version.created_by_id != version.agent.owner_id:
                failures.append(f"agent_version={version.id}: creator differs from agent tenant")

        for team in AgentTeam.objects.select_related("owner", "project", "director").iterator(chunk_size=200):
            if team.director.owner_id != team.owner_id:
                failures.append(f"team={team.id}: director belongs to another tenant")
            if team.project_id and team.project.owner_id != team.owner_id:
                failures.append(f"team={team.id}: project belongs to another tenant")

        for member in AgentTeamMember.objects.select_related("team", "agent").iterator(chunk_size=500):
            if member.agent.owner_id != member.team.owner_id:
                failures.append(f"team_member={member.id}: agent belongs to another tenant")
            if member.team.project_id and member.agent.project_id not in {None, member.team.project_id}:
                failures.append(f"team_member={member.id}: agent belongs to another project")

        for run in AgentRun.objects.select_related("owner", "agent", "team", "project").iterator(chunk_size=500):
            subject = run.agent or run.team
            if subject is None:
                failures.append(f"run={run.id}: subject missing")
                continue
            if subject.owner_id != run.owner_id:
                failures.append(f"run={run.id}: subject belongs to another tenant")
            if run.project_id and run.project.owner_id != run.owner_id:
                failures.append(f"run={run.id}: project belongs to another tenant")

        for approval in AgentApproval.objects.select_related("run", "requested_by_agent").iterator(chunk_size=500):
            if approval.requested_by_agent_id and approval.requested_by_agent.owner_id != approval.run.owner_id:
                failures.append(f"approval={approval.id}: requesting agent belongs to another tenant")

        for schedule in AgentSchedule.objects.select_related("owner", "agent", "team").iterator(chunk_size=200):
            subject = schedule.agent or schedule.team
            if subject is None or subject.owner_id != schedule.owner_id:
                failures.append(f"schedule={schedule.id}: subject/owner tenant mismatch")

        for trigger in AgentWebhookTrigger.objects.select_related("owner", "agent", "team").iterator(chunk_size=200):
            subject = trigger.agent or trigger.team
            if subject is None or subject.owner_id != trigger.owner_id:
                failures.append(f"webhook={trigger.id}: subject/owner tenant mismatch")
            try:
                identify_hasher(trigger.secret_hash)
            except (ValueError, TypeError):
                failures.append(f"webhook={trigger.id}: secret is not stored as a recognized password hash")

        for delivery in AgentWebhookDelivery.objects.select_related("trigger", "run").iterator(chunk_size=500):
            if delivery.run_id and delivery.run.owner_id != delivery.trigger.owner_id:
                failures.append(f"webhook_delivery={delivery.id}: linked run belongs to another tenant")

        self.stdout.write(
            " ".join(
                [
                    f"agents={Agent.objects.count()}",
                    f"teams={AgentTeam.objects.count()}",
                    f"runs={AgentRun.objects.count()}",
                    f"webhooks={AgentWebhookTrigger.objects.count()}",
                ]
            )
        )
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"[WARN] {warning}"))
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))

        if failures:
            raise CommandError(f"Agent security audit failed: {len(failures)} problem(s)")
        self.stdout.write(self.style.SUCCESS("AGENT_SECURITY_AUDIT_OK"))
