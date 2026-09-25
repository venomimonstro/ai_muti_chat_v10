from celery import shared_task

from .generic_team_runtime import execute_generic_team_run
from .models import AgentRun, AgentTeam
from .runtime import execute_run
from .team_runtime import execute_team_run


@shared_task(bind=True, max_retries=0, soft_time_limit=900, time_limit=930)
def execute_agent_run_task(self, run_id):
    subject = (
        AgentRun.objects.filter(pk=run_id)
        .values("team_id", "team__kind", "team__director__role")
        .first()
    )
    if subject is None:
        return {"run_id": str(run_id), "state": "missing"}
    if not subject["team_id"]:
        run = execute_run(run_id)
    else:
        role = str(subject.get("team__director__role") or "").strip().casefold()
        is_legacy_dev = role == "engineering director"
        if subject["team__kind"] == AgentTeam.Kind.DEVELOPMENT or is_legacy_dev:
            run = execute_team_run(run_id)
        else:
            run = execute_generic_team_run(run_id)
    return {"run_id": str(run.id), "state": run.state}
