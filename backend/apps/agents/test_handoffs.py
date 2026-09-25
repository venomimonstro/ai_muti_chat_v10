import pytest

from apps.accounts.models import User

from .models import Agent, AgentHandoff, AgentRun, AgentStepRun, AgentTeam, AgentTeamMember
from .serializers import AgentRunSerializer


@pytest.mark.django_db
def test_completed_team_steps_create_one_auditable_handoff():
    user = User.objects.create_user(username="handoff-owner", email="handoff-owner@example.com", password="StrongPass123!")
    first = Agent.objects.create(owner=user, name="Strategist", role="Strategist", objective="Plan", status=Agent.Status.ACTIVE)
    second = Agent.objects.create(owner=user, name="Writer", role="Writer", objective="Write", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=user, name="Content team", objective="Create content", director=first)
    AgentTeamMember.objects.create(team=team, agent=first, role="Strategist", priority=10, enabled=True)
    AgentTeamMember.objects.create(team=team, agent=second, role="Writer", priority=20, enabled=True)
    run = AgentRun.objects.create(owner=user, team=team, objective="Create post", state=AgentRun.State.RUNNING)

    AgentStepRun.objects.create(
        run=run,
        agent=first,
        sequence=1,
        node_id="team-member-1",
        title="Strategist",
        action_type="team_llm",
        state=AgentStepRun.State.COMPLETED,
        output_payload={"text": "Strategy result"},
        public_log="Strategy result",
    )
    second_step = AgentStepRun.objects.create(
        run=run,
        agent=second,
        sequence=2,
        node_id="team-member-2",
        title="Writer",
        action_type="team_llm",
        state=AgentStepRun.State.COMPLETED,
        output_payload={"text": "Final post"},
        public_log="Final post",
    )

    assert AgentHandoff.objects.filter(run=run).count() == 1
    handoff = AgentHandoff.objects.get(run=run)
    assert handoff.from_agent == first
    assert handoff.to_agent == second
    assert handoff.context["text"] == "Strategy result"
    assert handoff.result["text"] == "Final post"
    assert handoff.completed_at is not None

    second_step.save(update_fields=["public_log"])
    assert AgentHandoff.objects.filter(run=run).count() == 1

    payload = AgentRunSerializer(run).data
    assert len(payload["handoffs"]) == 1
    assert payload["handoffs"][0]["from_agent_name"] == "Strategist"
    assert payload["handoffs"][0]["to_agent_name"] == "Writer"
