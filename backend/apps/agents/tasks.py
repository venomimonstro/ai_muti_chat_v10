from celery import shared_task

from .runtime import execute_run


@shared_task(bind=True, max_retries=0, soft_time_limit=300, time_limit=330)
def execute_agent_run_task(self, run_id):
    run = execute_run(run_id)
    return {"run_id": str(run.id), "state": run.state}
