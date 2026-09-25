from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.accounts.models import User

from .models import Agent, AgentRun, AgentStepRun
from .planner import graph_for_kind
from .publish_runtime import finalize_graph_publish_nodes


@pytest.mark.django_db
def test_smm_and_seo_templates_default_publish_nodes_to_draft():
    for kind in ("smm", "seo"):
        graph = graph_for_kind(kind)
        publish_nodes = [node for node in graph["nodes"] if node.get("type") == "publish"]
        assert len(publish_nodes) == 1
        assert publish_nodes[0]["status"] == "draft"


@pytest.mark.django_db
def test_legacy_publish_node_without_status_creates_wordpress_draft():
    user = User.objects.create_user(username="publish-safe", email="publish-safe@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Publisher",
        objective="Prepare article",
        status=Agent.Status.ACTIVE,
        autonomy=Agent.Autonomy.SEMI_AUTONOMOUS,
        tool_policy={"publish": "approval"},
        graph={
            "version": 1,
            "nodes": [
                {"id": "draft", "title": "Draft", "type": "llm"},
                {"id": "publish", "title": "Publish", "type": "publish"},
            ],
            "edges": [{"from": "draft", "to": "publish"}],
        },
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Prepare article",
        state=AgentRun.State.COMPLETED,
    )
    AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=1,
        node_id="draft",
        title="Draft",
        action_type="llm",
        state=AgentStepRun.State.COMPLETED,
        output_payload={"text": "# Safe article\n\nContent"},
    )
    publish_step = AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=2,
        node_id="publish",
        title="Publish",
        action_type="publish",
        state=AgentStepRun.State.SKIPPED,
    )
    connection = SimpleNamespace(id="connection-1", name="WordPress")

    with (
        patch("apps.agents.publish_runtime._can_publish", return_value=True),
        patch("apps.agents.publish_runtime._wordpress_connection", return_value=connection),
        patch("apps.agents.publish_runtime.create_wordpress_post") as create_post,
    ):
        create_post.return_value = {"post_id": 42, "status": "draft", "existing": False}
        result = finalize_graph_publish_nodes(run.id)

    assert result.state == AgentRun.State.COMPLETED
    create_post.assert_called_once()
    assert create_post.call_args.kwargs["status"] == "draft"
    publish_step.refresh_from_db()
    assert publish_step.state == AgentStepRun.State.COMPLETED
    assert publish_step.output_payload["wordpress_publication"]["status"] == "draft"
