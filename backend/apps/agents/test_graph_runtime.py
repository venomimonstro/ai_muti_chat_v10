import pytest
from django.utils import timezone

from apps.accounts.models import User

from .graph_runtime import execute_graph_run
from .models import Agent, AgentApproval, AgentRun, AgentStepRun


@pytest.mark.django_db
def test_visual_workflow_pauses_for_approval_and_resumes_safely():
    user = User.objects.create_user(username="graph-owner", email="graph-owner@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Publisher",
        objective="Prepare and publish content",
        status=Agent.Status.ACTIVE,
        graph={
            "nodes": [
                {"id": "approval", "title": "Подтвердить публикацию", "type": "approval"},
                {"id": "publish", "title": "Опубликовать", "type": "publish"},
            ],
            "edges": [{"from": "approval", "to": "publish"}],
            "version": 1,
        },
    )
    run = AgentRun.objects.create(owner=user, agent=agent, objective="Publish today's post")

    paused = execute_graph_run(run.id)
    assert paused.state == AgentRun.State.WAITING_APPROVAL
    approval = AgentApproval.objects.get(run=run)
    approval_step = AgentStepRun.objects.get(run=run, node_id="approval")
    assert approval_step.state == AgentStepRun.State.WAITING_APPROVAL

    approval.status = AgentApproval.Status.APPROVED
    approval.decided_by = user
    approval.decided_at = timezone.now()
    approval.save(update_fields=["status", "decided_by", "decided_at"])
    approval_step.refresh_from_db()
    assert approval_step.state == AgentStepRun.State.COMPLETED

    AgentRun.objects.filter(pk=run.id).update(state=AgentRun.State.QUEUED, finished_at=None)
    resumed = execute_graph_run(run.id)
    assert resumed.state == AgentRun.State.COMPLETED
    publish_step = AgentStepRun.objects.get(run=run, node_id="publish")
    assert publish_step.state == AgentStepRun.State.SKIPPED
    assert "не подключён" in publish_step.public_log


@pytest.mark.django_db
def test_rejected_approval_marks_waiting_step_skipped():
    user = User.objects.create_user(username="graph-reject", email="graph-reject@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Controlled agent",
        objective="Do controlled work",
        status=Agent.Status.ACTIVE,
        graph={"nodes": [{"id": "approval", "title": "Нужно решение", "type": "approval"}]},
    )
    run = AgentRun.objects.create(owner=user, agent=agent, objective="Task")
    execute_graph_run(run.id)
    approval = AgentApproval.objects.get(run=run)
    approval.status = AgentApproval.Status.REJECTED
    approval.decided_by = user
    approval.decided_at = timezone.now()
    approval.save(update_fields=["status", "decided_by", "decided_at"])

    step = AgentStepRun.objects.get(run=run, node_id="approval")
    assert step.state == AgentStepRun.State.SKIPPED
