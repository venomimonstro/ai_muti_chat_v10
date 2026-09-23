from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.github_integration.models import GitHubInstallation, GitHubRepositoryBinding
from apps.projects.models import Project

from .models import Agent, AgentRun, AgentTeam
from .tasks import execute_agent_run_task


@pytest.mark.django_db
def test_agent_api_is_owner_scoped_and_template_creation_is_private():
    owner = User.objects.create_user(username="agent-owner", email="agent-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="agent-other", email="agent-other@example.com", password="StrongPass123!")
    Agent.objects.create(owner=other, name="Чужой агент", role="private")

    client = APIClient()
    client.force_authenticate(owner)

    response = client.get("/api/v1/agents/")
    assert response.status_code == 200
    assert response.json() == []

    created = client.post(
        "/api/v1/agents/from-template/",
        {"template": "smm-specialist", "name": "Мой SMM"},
        format="json",
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["name"] == "Мой SMM"
    assert payload["graph"]["nodes"]
    assert payload["graph"]["edges"]
    assert Agent.objects.filter(owner=owner, name="Мой SMM").exists()
    assert not Agent.objects.filter(owner=owner, name="Чужой агент").exists()


@pytest.mark.django_db
def test_agent_can_be_created_from_plain_language_and_bound_to_owned_project():
    owner = User.objects.create_user(username="agent-natural", email="agent-natural@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=owner, name="Контент")
    client = APIClient()
    client.force_authenticate(owner)

    created = client.post(
        "/api/v1/agents/from-description/",
        {
            "description": "Нужен SMM специалист: ищет темы, пишет посты, делает изображения и просит подтверждение перед публикацией.",
            "project": str(project.id),
        },
        format="json",
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["project"] == str(project.id)
    assert payload["role"] == "SMM specialist"
    assert payload["tool_policy"]["publish"] == "approval"
    assert any(node["type"] == "approval" for node in payload["graph"]["nodes"])


@pytest.mark.django_db
def test_agent_cannot_bind_to_another_users_project():
    owner = User.objects.create_user(username="agent-own-project", email="agent-own-project@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="agent-foreign-project", email="agent-foreign-project@example.com", password="StrongPass123!")
    foreign_project = Project.objects.create(owner=other, name="Чужой проект")
    client = APIClient()
    client.force_authenticate(owner)

    created = client.post(
        "/api/v1/agents/from-description/",
        {"description": "Нужен сотрудник для анализа документов и подготовки отчётов", "project": str(foreign_project.id)},
        format="json",
    )

    assert created.status_code == 400
    assert not Agent.objects.filter(owner=owner).exists()


@pytest.mark.django_db
def test_agent_run_requires_activation_and_is_queued_once():
    user = User.objects.create_user(username="agent-runner", email="agent-runner@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Разработчик", objective="Исправлять проект")
    client = APIClient()
    client.force_authenticate(user)

    blocked = client.post(f"/api/v1/agents/{agent.id}/run/", {}, format="json")
    assert blocked.status_code == 400

    activated = client.post(f"/api/v1/agents/{agent.id}/activate/", {}, format="json")
    assert activated.status_code == 200

    with patch("apps.agents.tasks.execute_agent_run_task.delay") as delay:
        started = client.post(f"/api/v1/agents/{agent.id}/run/", {"objective": "Провести аудит"}, format="json")

    assert started.status_code == 201
    assert started.json()["state"] == "queued"
    run = AgentRun.objects.get(pk=started.json()["id"])
    delay.assert_called_once_with(str(run.id))


@pytest.mark.django_db
def test_run_list_is_filtered_by_agent_and_owner():
    user = User.objects.create_user(username="agent-filter", email="agent-filter@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="agent-filter-other", email="agent-filter-other@example.com", password="StrongPass123!")
    first = Agent.objects.create(owner=user, name="Первый")
    second = Agent.objects.create(owner=user, name="Второй")
    foreign = Agent.objects.create(owner=other, name="Чужой")
    wanted = AgentRun.objects.create(owner=user, agent=first, objective="one")
    AgentRun.objects.create(owner=user, agent=second, objective="two")
    AgentRun.objects.create(owner=other, agent=foreign, objective="foreign")
    client = APIClient()
    client.force_authenticate(user)

    response = client.get(f"/api/v1/agent-runs/?agent={first.id}")

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == [str(wanted.id)]


@pytest.mark.django_db
def test_dev_team_requires_owned_project_with_github_binding():
    user = User.objects.create_user(username="dev-team", email="dev-team@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=user, name="Repo project")
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/agent-teams/bootstrap-dev/",
        {"objective": "Исправить проект", "project": str(project.id)},
        format="json",
    )

    assert response.status_code == 400
    assert not AgentTeam.objects.filter(owner=user).exists()


@pytest.mark.django_db
def test_dev_team_bootstrap_creates_four_owned_project_agents():
    user = User.objects.create_user(username="dev-team-ready", email="dev-team-ready@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=user, name="Ready repo")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=77101,
        account_login="dev-team-ready",
        account_type="User",
        permissions={"contents": "write"},
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=88101,
        full_name="dev-team-ready/repo",
        default_branch="main",
        write_enabled=True,
    )
    client = APIClient()
    client.force_authenticate(user)

    created = client.post(
        "/api/v1/agent-teams/bootstrap-dev/",
        {"objective": "Исправить проект и проверить тесты", "project": str(project.id)},
        format="json",
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["project"] == str(project.id)
    assert len(payload["members"]) == 4
    assert {item["role"] for item in payload["members"]} == {
        "Engineering Director",
        "Architecture",
        "Development",
        "QA & Security",
    }
    assert Agent.objects.filter(owner=user, project=project).count() == 4


@pytest.mark.django_db
def test_celery_task_routes_team_runs_to_team_runtime():
    user = User.objects.create_user(username="task-route", email="task-route@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=user, name="Task route")
    director = Agent.objects.create(owner=user, project=project, name="Director", role="Engineering Director")
    team = AgentTeam.objects.create(owner=user, project=project, name="Team", director=director, objective="Do work")
    run = AgentRun.objects.create(owner=user, team=team, project=project, objective="Do work")

    with patch("apps.agents.tasks.execute_team_run", return_value=run) as team_runtime, patch(
        "apps.agents.tasks.execute_run"
    ) as single_runtime:
        result = execute_agent_run_task.run(str(run.id))

    assert result == {"run_id": str(run.id), "state": run.state}
    team_runtime.assert_called_once_with(str(run.id))
    single_runtime.assert_not_called()
