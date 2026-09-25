from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.connections.models import AgentConnectionBinding, ExternalConnection

from .models import Agent, AgentApproval, AgentRun, AgentStepRun
from .publish_runtime import finalize_graph_publish_nodes


def _fixture(*, approved=True, with_connection=True):
    user = get_user_model().objects.create_user(
        username=f"publisher-{approved}-{with_connection}",
        email=f"publisher-{approved}-{with_connection}@example.test",
        password="test-password",
    )
    agent = Agent.objects.create(
        owner=user,
        name="SEO Publisher",
        objective="Write and publish",
        autonomy=Agent.Autonomy.SEMI_AUTONOMOUS,
        status=Agent.Status.ACTIVE,
        tool_policy={"web": True, "publish": "approval"},
        graph={
            "nodes": [
                {"id": "draft", "title": "Draft", "type": "llm"},
                {"id": "approve", "title": "Approve", "type": "approval"},
                {"id": "publish", "title": "Publish", "type": "publish", "status": "publish"},
            ],
            "edges": [],
        },
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Publish article",
        state=AgentRun.State.COMPLETED,
        output_payload={"text": "# Final article\n\nUseful content"},
        finished_at=timezone.now(),
    )
    AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=1,
        node_id="draft",
        title="Draft",
        action_type="llm",
        state=AgentStepRun.State.COMPLETED,
        output_payload={"text": "# Final article\n\nUseful content"},
        public_log="Final article",
        started_at=timezone.now(),
        finished_at=timezone.now(),
    )
    approval_step = AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=2,
        node_id="approve",
        title="Approve",
        action_type="approval",
        state=AgentStepRun.State.COMPLETED if approved else AgentStepRun.State.WAITING_APPROVAL,
        started_at=timezone.now(),
        finished_at=timezone.now() if approved else None,
    )
    if approved:
        AgentApproval.objects.create(
            run=run,
            step=approval_step,
            requested_by_agent=agent,
            title="Approve publication",
            status=AgentApproval.Status.APPROVED,
            decided_by=user,
            decided_at=timezone.now(),
            action_payload={"kind": "workflow_approval", "node_id": "approve"},
        )
    publish_step = AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=3,
        node_id="publish",
        title="Publish",
        action_type="publish",
        state=AgentStepRun.State.SKIPPED,
        public_log="External action deferred",
        started_at=timezone.now(),
        finished_at=timezone.now(),
    )
    connection = None
    if with_connection:
        connection = ExternalConnection.objects.create(
            owner=user,
            kind=ExternalConnection.Kind.WORDPRESS,
            name="Main site",
            base_url="https://example.com",
            username="editor",
            enabled=True,
            health_state=ExternalConnection.Health.HEALTHY,
        )
        connection.set_secret("application-password")
        connection.save(update_fields=["secret_encrypted"])
        AgentConnectionBinding.objects.create(
            agent=agent,
            connection=connection,
            purpose="publish",
            enabled=True,
        )
    return run, publish_step


@pytest.mark.django_db(transaction=True)
def test_publish_requires_approved_workflow_step():
    run, step = _fixture(approved=False, with_connection=True)
    with patch("apps.agents.publish_runtime.create_wordpress_post") as create_post:
        result = finalize_graph_publish_nodes(run.id)
    result.refresh_from_db()
    step.refresh_from_db()
    assert result.state == AgentRun.State.FAILED
    assert result.error_code == "wordpress_publish_failed"
    assert step.state == AgentStepRun.State.FAILED
    create_post.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_publish_requires_healthy_bound_wordpress():
    run, step = _fixture(approved=True, with_connection=False)
    with patch("apps.agents.publish_runtime.create_wordpress_post") as create_post:
        result = finalize_graph_publish_nodes(run.id)
    result.refresh_from_db()
    assert result.state == AgentRun.State.FAILED
    assert result.error_code == "wordpress_publish_failed"
    create_post.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_approved_publish_is_idempotent():
    run, step = _fixture(approved=True, with_connection=True)
    with patch(
        "apps.agents.publish_runtime.create_wordpress_post",
        return_value={"post_id": 42, "status": "publish", "url": "https://example.com/post", "slug": "post"},
    ) as create_post:
        first = finalize_graph_publish_nodes(run.id)
        second = finalize_graph_publish_nodes(run.id)

    first.refresh_from_db()
    second.refresh_from_db()
    step.refresh_from_db()
    assert first.state == AgentRun.State.COMPLETED
    assert second.state == AgentRun.State.COMPLETED
    assert step.state == AgentStepRun.State.COMPLETED
    assert step.output_payload["wordpress_publication"]["post_id"] == 42
    assert second.output_payload["wordpress_publication"]["post_id"] == 42
    assert second.tool_call_count == 1
    create_post.assert_called_once()
