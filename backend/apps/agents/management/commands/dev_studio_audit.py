from django.core.management.base import BaseCommand, CommandError

from apps.agents.models import AgentRun, AgentTeam
from apps.agents.team_readiness import team_readiness


ACTIVE_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}


class Command(BaseCommand):
    help = "Audit active Dev Studio teams against the real production preflight"

    def handle(self, *args, **options):
        failures = []
        warnings = []
        teams = (
            AgentTeam.objects.filter(kind=AgentTeam.Kind.DEVELOPMENT)
            .select_related("owner", "director", "project", "project__github_repository__installation")
            .prefetch_related("members__agent")
        )

        self.stdout.write("=== DEV STUDIO AUDIT ===")
        active_count = 0
        ready_count = 0
        for team in teams:
            if not team.active:
                warnings.append(f"team={team.id}: paused; readiness is not release-blocking")
                continue
            active_count += 1
            result = team_readiness(team)
            if result["ready"]:
                ready_count += 1
                self.stdout.write(self.style.SUCCESS(f"[OK] team={team.id} name={team.name}"))
                continue
            for blocker in result["blockers"]:
                failures.append(f"team={team.id}: {blocker}")

        active_runs = AgentRun.objects.filter(
            team__kind=AgentTeam.Kind.DEVELOPMENT,
            state__in=ACTIVE_STATES,
        ).count()
        self.stdout.write(
            f"dev_teams={teams.count()} active_dev_teams={active_count} ready_dev_teams={ready_count} active_dev_runs={active_runs}"
        )
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"[WARN] {warning}"))
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))

        if failures:
            raise CommandError(f"Dev Studio audit failed: {len(failures)} blocker(s)")
        self.stdout.write(self.style.SUCCESS("DEV_STUDIO_AUDIT_OK"))
