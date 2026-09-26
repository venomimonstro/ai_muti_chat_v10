import pytest
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentRun, AgentTeam
from .webhook_models import AgentWebhookTrigger


@pytest.fixture
def tenants():
    owner = User.objects.create_user(username="tenant-a", email="tenant-a@example.com", password="StrongPass123!")
    attacker = User.objects.create_user(username="tenant-b", email="tenant-b@example.com", password="StrongPass123!")
    return owner, attacker


@pytest.mark.django_db
def test_agent_object_idor_is_denied_across_read_write_and_run(tenants):
    owner, attacker = tenants
    foreign_agent = Agent.objects.create(
        owner=owner,
        name="Private agent",
        objective="Private objective",
        status=Agent.Status.ACTIVE,
    )
    client = APIClient()
    client.force_authenticate(attacker)

    assert client.get(f"/api/v1/agents/{foreign_agent.id}/").status_code == 404
    assert client.patch(f"/api/v1/agents/{foreign_agent.id}/", {"name": "stolen"}, format="json").status_code == 404
    assert client.delete(f"/api/v1/agents/{foreign_agent.id}/").status_code == 404
    assert client.post(f"/api/v1/agents/{foreign_agent.id}/run/", {"objective": "steal"}, format="json").status_code == 404

    foreign_agent.refresh_from_db()
    assert foreign_agent.name == "Private agent"
    assert not AgentRun.objects.filter(owner=attacker).exists()


@pytest.mark.django_db
def test_run_idor_is_denied_for_detail_cancel_and_repeat(tenants):
    owner, attacker = tenants
    agent = Agent.objects.create(owner=owner, name="Agent", objective="Private", status=Agent.Status.ACTIVE)
    run = AgentRun.objects.create(owner=owner, agent=agent, objective="Private run", state=AgentRun.State.QUEUED)
    client = APIClient()
    client.force_authenticate(attacker)

    assert client.get(f"/api/v1/agent-runs/{run.id}/").status_code == 404
    assert client.post(f"/api/v1/agent-runs/{run.id}/cancel/", {}, format="json").status_code == 404
    assert client.post(f"/api/v1/agent-runs/{run.id}/repeat/", {}, format="json").status_code == 404

    run.refresh_from_db()
    assert run.state == AgentRun.State.QUEUED


@pytest.mark.django_db
def test_team_and_webhook_management_are_owner_scoped(tenants):
    owner, attacker = tenants
    director = Agent.objects.create(owner=owner, name="Director", objective="Private", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=owner, name="Private team", objective="Private", director=director)
    trigger = AgentWebhookTrigger.objects.create(
        owner=owner,
        agent=director,
        name="Private hook",
        objective="Private",
        secret_hash=make_password("secret-value"),
    )
    client = APIClient()
    client.force_authenticate(attacker)

    assert client.get(f"/api/v1/agent-teams/{team.id}/").status_code == 404
    assert client.patch(f"/api/v1/agent-teams/{team.id}/", {"name": "stolen"}, format="json").status_code == 404
    assert client.post(f"/api/v1/agent-teams/{team.id}/run/", {}, format="json").status_code == 404
    assert client.get(f"/api/v1/agent-webhooks/{trigger.id}/").status_code == 404
    assert client.post(f"/api/v1/agent-webhooks/{trigger.id}/rotate-secret/", {}, format="json").status_code == 404

    team.refresh_from_db()
    trigger.refresh_from_db()
    assert team.name == "Private team"
    assert trigger.owner_id == owner.id


@pytest.mark.django_db
def test_agent_api_rejects_unsafe_code_tool_policy(tenants):
    owner, _attacker = tenants
    client = APIClient()
    client.force_authenticate(owner)

    response = client.post(
        "/api/v1/agents/",
        {
            "name": "Unsafe developer",
            "objective": "Develop",
            "tool_policy": {"write_code": True, "github": True, "shell": "disabled", "merge": "disabled"},
        },
        format="json",
    )

    assert response.status_code == 400
    assert not Agent.objects.filter(owner=owner, name="Unsafe developer").exists()


@pytest.mark.django_db
def test_agent_security_audit_rejects_cross_tenant_run(tenants):
    owner, attacker = tenants
    foreign_agent = Agent.objects.create(owner=owner, name="Foreign", objective="Private", status=Agent.Status.ACTIVE)
    AgentRun.objects.create(owner=attacker, agent=foreign_agent, objective="Corrupt run", state=AgentRun.State.QUEUED)

    with pytest.raises(CommandError, match="Agent security audit failed"):
        call_command("agent_security_audit")


@pytest.mark.django_db
def test_agent_security_audit_rejects_unsafe_shell_policy(tenants):
    owner, _attacker = tenants
    Agent.objects.create(
        owner=owner,
        name="Unsafe developer",
        objective="Develop",
        status=Agent.Status.ACTIVE,
        tool_policy={"write_code": True, "shell": "host", "merge": "auto"},
    )

    with pytest.raises(CommandError, match="Agent security audit failed"):
        call_command("agent_security_audit")


@pytest.mark.django_db
def test_agent_security_audit_accepts_safe_tenant_state(tenants):
    owner, _attacker = tenants
    agent = Agent.objects.create(
        owner=owner,
        name="Safe developer",
        objective="Develop",
        status=Agent.Status.ACTIVE,
        tool_policy={"github": True, "write_code": True, "shell": "sandbox", "merge": "approval"},
    )
    AgentWebhookTrigger.objects.create(
        owner=owner,
        agent=agent,
        name="Safe hook",
        objective="Process",
        secret_hash=make_password("secret-value"),
    )

    call_command("agent_security_audit")
