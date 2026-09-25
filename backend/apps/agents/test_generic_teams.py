from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import AgentRun, AgentTeam
from .tasks import execute_agent_run_task
from .team_builder import infer_team_kind


def test_team_kind_classifier_separates_content_marketing_and_sales():
    assert infer_team_kind("контент-команда для статей, SEO и редакторской проверки") == AgentTeam.Kind.CONTENT
    assert infer_team_kind("SMM отдел для соцсетей, рекламы и продвижения бренда") == AgentTeam.Kind.MARKETING
    assert infer_team_kind("отдел продаж для лидов, CRM и клиентов") == AgentTeam.Kind.SALES
    assert infer_team_kind("команда разработчиков GitHub backend frontend") == AgentTeam.Kind.DEVELOPMENT


@pytest.mark.django_db
def test_team_can_be_created_from_plain_language_without_project():
    user = User.objects.create_user(username="team-natural", email="team-natural@example.com", password="StrongPass123!")
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/agent-teams/from-description/",
        {"description": "Нужен маркетинговый отдел: анализировать конкурентов, делать SMM-план, рекламу и проверять факты"},
        format="json",
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["kind"] == AgentTeam.Kind.MARKETING
    assert payload["project"] is None
    assert len(payload["members"]) >= 4
    assert payload["members"][0]["can_delegate"] is True
    assert AgentTeam.objects.filter(owner=user, kind=AgentTeam.Kind.MARKETING).exists()


@pytest.mark.django_db
def test_content_team_is_not_misclassified_as_marketing():
    user = User.objects.create_user(username="team-content-natural", email="team-content-natural@example.com", password="StrongPass123!")
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        "/api/v1/agent-teams/from-description/",
        {"description": "Создай контент-команду для подготовки SEO статей, копирайтинга и редакторской проверки"},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["kind"] == AgentTeam.Kind.CONTENT


@pytest.mark.django_db
def test_development_description_is_redirected_to_dev_studio():
    user = User.objects.create_user(username="team-dev-natural", email="team-dev-natural@example.com", password="StrongPass123!")
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/agent-teams/from-description/",
        {"description": "Нужна команда разработчиков для GitHub: backend, frontend и тестирование проекта"},
        format="json",
    )

    assert response.status_code == 400
    assert AgentTeam.objects.filter(owner=user).count() == 0


@pytest.mark.django_db
def test_generic_team_run_routes_to_generic_runtime():
    user = User.objects.create_user(username="generic-team-route", email="generic-team-route@example.com", password="StrongPass123!")
    client = APIClient()
    client.force_authenticate(user)
    created = client.post(
        "/api/v1/agent-teams/from-description/",
        {"description": "Создай контент-команду для подготовки статей, исследования и редакторской проверки"},
        format="json",
    )
    assert created.status_code == 201
    team = AgentTeam.objects.get(pk=created.json()["id"])
    assert team.kind == AgentTeam.Kind.CONTENT
    run = AgentRun.objects.create(owner=user, team=team, objective="Подготовить статью")

    with patch("apps.agents.tasks.execute_generic_team_run", return_value=run) as generic_runtime, patch(
        "apps.agents.tasks.execute_team_run"
    ) as dev_runtime:
        result = execute_agent_run_task.run(str(run.id))

    assert result == {"run_id": str(run.id), "state": run.state}
    generic_runtime.assert_called_once_with(str(run.id))
    dev_runtime.assert_not_called()


@pytest.mark.django_db
def test_legacy_engineering_director_team_still_routes_to_dev_runtime():
    user = User.objects.create_user(username="legacy-dev-route", email="legacy-dev-route@example.com", password="StrongPass123!")
    from .models import Agent

    director = Agent.objects.create(owner=user, name="Engineering Director", role="Engineering Director")
    team = AgentTeam.objects.create(owner=user, name="Legacy Dev Team", objective="Fix code", director=director, kind=AgentTeam.Kind.GENERIC)
    run = AgentRun.objects.create(owner=user, team=team, objective="Fix code")

    with patch("apps.agents.tasks.execute_team_run", return_value=run) as dev_runtime, patch(
        "apps.agents.tasks.execute_generic_team_run"
    ) as generic_runtime:
        result = execute_agent_run_task.run(str(run.id))

    assert result == {"run_id": str(run.id), "state": run.state}
    dev_runtime.assert_called_once_with(str(run.id))
    generic_runtime.assert_not_called()
