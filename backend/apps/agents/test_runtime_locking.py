import pytest
from django.db import transaction

from apps.accounts.models import User

from .models import Agent, AgentRun


@pytest.mark.django_db(transaction=True)
def test_agent_run_start_lock_targets_only_run_row():
    user = User.objects.create_user(
        username="runtime-lock-user",
        email="runtime-lock@example.test",
        password="test-password-123",
    )
    agent = Agent.objects.create(owner=user, name="Runtime lock agent")
    run = AgentRun.objects.create(owner=user, agent=agent, objective="Проверить блокировку запуска")

    with transaction.atomic():
        locked = (
            AgentRun.objects.select_for_update(of=("self",))
            .select_related("owner", "agent", "team__director", "project")
            .get(pk=run.pk)
        )

    assert locked.pk == run.pk
    assert locked.agent_id == agent.pk
    assert locked.team_id is None
    assert locked.project_id is None
