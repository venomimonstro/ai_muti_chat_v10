import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentTeam, AgentTeamMember


@pytest.mark.django_db
def test_new_director_becomes_first_execution_stage_and_can_delegate():
    user = User.objects.create_user(username="director-order", email="director-order@example.com", password="StrongPass123!")
    first = Agent.objects.create(owner=user, name="First", objective="Work", status=Agent.Status.ACTIVE)
    second = Agent.objects.create(owner=user, name="Second", objective="Work", status=Agent.Status.ACTIVE)
    third = Agent.objects.create(owner=user, name="Third", objective="Work", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=user, name="Team", objective="Work together", director=first)
    first_member = AgentTeamMember.objects.create(team=team, agent=first, role="First role", priority=10, can_delegate=True)
    second_member = AgentTeamMember.objects.create(team=team, agent=second, role="Second role", priority=20)
    third_member = AgentTeamMember.objects.create(team=team, agent=third, role="Third role", priority=30)

    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/agent-teams/{team.id}/director/",
        {"agent": str(third.id)},
        format="json",
    )

    assert response.status_code == 200
    team.refresh_from_db()
    assert team.director_id == third.id
    first_member.refresh_from_db()
    second_member.refresh_from_db()
    third_member.refresh_from_db()
    ordered = list(team.members.filter(enabled=True).order_by("priority", "role").values_list("agent_id", "priority"))
    assert ordered == [(third.id, 10), (first.id, 20), (second.id, 30)]
    assert third_member.can_delegate is True


@pytest.mark.django_db
def test_director_cannot_be_moved_after_another_active_member():
    user = User.objects.create_user(username="director-priority", email="director-priority@example.com", password="StrongPass123!")
    director = Agent.objects.create(owner=user, name="Director", objective="Direct", status=Agent.Status.ACTIVE)
    worker = Agent.objects.create(owner=user, name="Worker", objective="Work", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=user, name="Team", objective="Work together", director=director)
    director_member = AgentTeamMember.objects.create(team=team, agent=director, role="Director", priority=10, can_delegate=True)
    AgentTeamMember.objects.create(team=team, agent=worker, role="Worker", priority=20)

    client = APIClient()
    client.force_authenticate(user)
    response = client.patch(
        f"/api/v1/agent-teams/{team.id}/members/{director_member.id}/",
        {"priority": 100},
        format="json",
    )

    assert response.status_code == 400
    director_member.refresh_from_db()
    assert director_member.priority == 10
