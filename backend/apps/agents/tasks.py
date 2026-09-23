from celery import shared_task

from .models import AgentRun
from .runtime import execute_run
from .team_runtime import execute_team_run


@shared_task(bind=True, max_retries=0, soft_time_limit=900, time_limit=930)
def execute_agent_run_task(self, run_id):
    subject = AgentRun.objects.filter(pk=run_id).values("team_id").first()
    if subject is None:
        return {"run_id": str(run_id), "state": "missing"}
    run = execute_team_run(run_id) if subject["team_id"] else execute_run(run_id)
    return {"run_id": str(run.id), "state": run.state}
