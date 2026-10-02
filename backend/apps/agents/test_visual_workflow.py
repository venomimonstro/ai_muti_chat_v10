from unittest.mock import Mock

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.connections.models import AgentConnectionBinding, ExternalConnection

from .config_views import _validate_graph
from .graph_runtime_v2 import execute_graph_run_v2
from .models import Agent, AgentApproval, AgentRun
from .public_network import PublicNetworkError, public_target
from .workflow_tools import render_body


def test_public_transport_rejects_private_and_mixed_dns(monkeypatch):
    for addresses in [['127.0.0.1'], ['8.8.8.8', '10.0.0.1']]:
        monkeypatch.setattr('apps.agents.public_network.socket.getaddrinfo', lambda *a, addresses=addresses, **kw: [(2, 1, 6, '', (ip, 443)) for ip in addresses])
        with pytest.raises(PublicNetworkError):
            public_target('https://example.test')


def test_template_substitution_is_data_not_code():
    assert render_body({'text': ['{{previous_text}}', '{{objective}}', 12]}, objective='Task', previous_text='Answer') == {'text': ['Answer', 'Task', 12]}


@pytest.mark.parametrize('node', [
    {'id': 'a', 'title': 'Browser', 'type': 'browser', 'url': 'file:///etc/passwd'},
    {'id': 'a', 'title': 'HTTP', 'type': 'http', 'connection_id': 'bad'},
    {'id': 'a', 'title': 'LLM', 'type': 'llm', 'position': {'x': float('inf'), 'y': 3}},
])
def test_invalid_new_nodes_rejected(node):
    from rest_framework.exceptions import ValidationError
    with pytest.raises(ValidationError):
        _validate_graph({'nodes': [node], 'edges': []})


@pytest.mark.django_db
def test_explicit_disconnected_route_does_not_run_next_node():
    user = User.objects.create_user(username='explicit-graph')
    agent = Agent.objects.create(owner=user, name='No AI', objective='Integration only', graph={'routing': 'explicit', 'nodes': [{'id': 'start', 'title': 'First', 'type': 'notify', 'message': 'First'}, {'id': 'unused', 'title': 'Unused', 'type': 'notify', 'message': 'Unused'}], 'edges': []})
    run = AgentRun.objects.create(owner=user, agent=agent, objective='Run', state='queued')
    result = execute_graph_run_v2(run.id)
    assert result.state == 'completed'
    assert not result.steps.filter(node_id='unused', state='completed').exists()


def setup_http(username, method='GET'):
    user = User.objects.create_user(username=username, email=f'{username}@example.test')
    connection = ExternalConnection.objects.create(owner=user, name='Service', kind='http', base_url='https://api.example.test', health_state='healthy')
    connection.set_secret('never-in-prompt')
    connection.save()
    graph = {'routing': 'explicit', 'nodes': [{'id': 'http', 'title': 'API call', 'type': 'http', 'connection_id': str(connection.id), 'method': method, 'path': 'v1/items', 'body': {'text': '{{objective}}'}}], 'edges': []}
    agent = Agent.objects.create(owner=user, name='HTTP agent', objective='Integration', tool_policy={'http': True}, graph=graph)
    AgentConnectionBinding.objects.create(agent=agent, connection=connection, purpose='http')
    return user, agent


@pytest.mark.django_db
def test_http_only_workflow_runs_without_llm_or_customer_charge(monkeypatch):
    user, agent = setup_http('http-only')
    request = Mock(return_value='{"success":true}')
    monkeypatch.setattr('apps.agents.workflow_tools.connection_request', request)
    run = AgentRun.objects.create(owner=user, agent=agent, objective='Hello', state='queued')
    result = execute_graph_run_v2(run.id)
    assert result.state == 'completed'
    assert result.cost_actual_rub == 0
    assert result.tool_call_count == 1
    assert request.call_count == 1
    assert result.steps.get(node_id='http').output_payload['text'] == '{"success":true}'


@pytest.mark.django_db
def test_post_waits_for_approval_and_completed_tool_is_not_replayed(monkeypatch):
    user, agent = setup_http('post-approval', 'POST')
    request = Mock(return_value='{"created":true}')
    monkeypatch.setattr('apps.agents.workflow_tools.connection_request', request)
    run = AgentRun.objects.create(owner=user, agent=agent, objective='Send this', state='queued')
    first = execute_graph_run_v2(run.id)
    assert first.state == 'waiting_approval'
    assert not request.called
    approval = first.approvals.get()
    approval.status = AgentApproval.Status.APPROVED
    approval.save()
    run.state = 'queued'
    run.save()
    second = execute_graph_run_v2(run.id)
    assert second.state == 'completed'
    assert request.call_count == 1
    assert request.call_args.kwargs['payload'] == {'text': 'Send this'}
    execute_graph_run_v2(run.id)
    assert request.call_count == 1


@pytest.mark.django_db
def test_connection_from_another_owner_cannot_be_saved():
    user, agent = setup_http('owner-a')
    other = User.objects.create_user(username='owner-b', email='owner-b@example.test')
    foreign = ExternalConnection.objects.create(owner=other, name='Foreign', kind='http', base_url='https://api.example.test')
    client = APIClient()
    client.force_authenticate(user)
    graph = agent.graph
    graph['nodes'][0]['connection_id'] = str(foreign.id)
    response = client.patch(f'/api/v1/agents/{agent.id}/config/', {'graph': graph}, format='json')
    assert response.status_code == 400


@pytest.mark.django_db
def test_planner_idempotency_replays_result_without_second_generation(monkeypatch):
    from rest_framework.response import Response

    from .ai_planner_views import AgentAIPlannerPreviewView
    from .models import AgentPlanOperation
    calls = Mock(return_value=Response({'draft': {'graph': {'nodes': []}}, 'cost_rub': '0.12'}))
    monkeypatch.setattr(AgentAIPlannerPreviewView, '_generate', calls)
    user = User.objects.create_user(username='planner-repeat', email='planner-repeat@example.test')
    client = APIClient()
    client.force_authenticate(user)
    body = {'description': 'Build a simple workflow from these supported nodes'}
    first = client.post('/api/v1/agents/ai-planner/preview/', body, format='json', HTTP_IDEMPOTENCY_KEY='same-plan')
    second = client.post('/api/v1/agents/ai-planner/preview/', body, format='json', HTTP_IDEMPOTENCY_KEY='same-plan')
    assert first.status_code == second.status_code == 200
    assert first.data == second.data
    assert calls.call_count == 1
    assert AgentPlanOperation.objects.count() == 1
    conflict = client.post('/api/v1/agents/ai-planner/preview/', {'description': 'different'}, format='json', HTTP_IDEMPOTENCY_KEY='same-plan')
    assert conflict.status_code == 409
    assert calls.call_count == 1


@pytest.mark.django_db
def test_http_uncertain_failure_is_not_automatically_repeated(monkeypatch):
    from django.core.exceptions import ValidationError
    user, agent = setup_http('post-uncertain', 'POST')
    request = Mock(side_effect=ValidationError('Connection lost after request'))
    monkeypatch.setattr('apps.agents.workflow_tools.connection_request', request)
    run = AgentRun.objects.create(owner=user, agent=agent, objective='Send this', state='queued')
    execute_graph_run_v2(run.id)
    approval = run.approvals.get()
    approval.status = AgentApproval.Status.APPROVED
    approval.save()
    run.state = 'queued'
    run.save()
    result = execute_graph_run_v2(run.id)
    assert result.state == 'failed'
    execute_graph_run_v2(run.id)
    assert request.call_count == 1


def test_connection_transport_does_not_follow_authenticated_redirect(monkeypatch):
    from types import SimpleNamespace

    from django.core.exceptions import ValidationError

    from apps.connections.http_service import connection_request
    monkeypatch.setattr('apps.agents.public_network.socket.getaddrinfo', lambda *args, **kwargs: [(2, 1, 6, '', ('8.8.8.8', 443))])
    request = Mock(return_value={'status': 302, 'body': b'', 'location': 'https://other.test/'})
    monkeypatch.setattr('apps.connections.http_service.public_request', request)
    connection = SimpleNamespace(base_url='https://api.example.test', get_secret=lambda: 'secret')
    with pytest.raises(ValidationError):
        connection_request(connection)
    assert request.call_count == 1
    assert request.call_args.kwargs['headers']['Authorization'] == 'Bearer secret'


@pytest.mark.django_db
def test_stale_graph_save_does_not_overwrite_newer_version():
    user = User.objects.create_user(username='stale-plan', email='stale-plan@example.test')
    agent = Agent.objects.create(owner=user, name='Agent', graph={'version': 3, 'nodes': [{'id': 'start', 'title': 'Newer', 'type': 'finish'}], 'edges': []})
    client = APIClient()
    client.force_authenticate(user)
    response = client.patch(f'/api/v1/agents/{agent.id}/config/', {'expected_graph_version': 2, 'graph': {'version': 3, 'nodes': [{'id': 'start', 'title': 'Stale', 'type': 'finish'}], 'edges': []}}, format='json')
    assert response.status_code == 409
    agent.refresh_from_db()
    assert agent.graph['nodes'][0]['title'] == 'Newer'


@pytest.mark.django_db
def test_blank_automation_cannot_start_as_implicit_llm(monkeypatch):
    from .readiness import agent_readiness
    monkeypatch.setattr('apps.agents.readiness._model_for', lambda agent: None)
    user = User.objects.create_user(username='blank-automation')
    agent = Agent.objects.create(owner=user, name='New process', role='Автоматизация', objective='Build a workflow', graph={})
    readiness = agent_readiness(agent)
    assert any('Добавьте и сохраните узлы' in blocker for blocker in readiness['blockers'])


@pytest.mark.django_db
def test_unkeyed_planner_preview_does_not_leave_completed_internal_operation(monkeypatch):
    from rest_framework.response import Response

    from .ai_planner_views import AgentAIPlannerPreviewView
    from .models import AgentPlanOperation

    calls = Mock(
        return_value=Response(
            {"draft": {"graph": {"nodes": []}}, "cost_rub": "0.12"}
        )
    )
    monkeypatch.setattr(AgentAIPlannerPreviewView, "_generate", calls)
    user = User.objects.create_user(
        username="planner-unkeyed-cleanup",
        email="planner-unkeyed-cleanup@example.test",
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/agents/ai-planner/preview/",
        {"description": "Build a supported workflow with safe approval steps"},
        format="json",
    )

    assert response.status_code == 200
    assert calls.call_count == 1
    assert AgentPlanOperation.objects.filter(owner=user).count() == 0


@pytest.mark.django_db
def test_planner_cleanup_failure_never_turns_successful_preview_into_500(monkeypatch):
    from rest_framework.response import Response

    from .ai_planner_views import AgentAIPlannerPreviewView
    from .models import AgentPlanOperation

    calls = Mock(
        return_value=Response(
            {"draft": {"graph": {"nodes": []}}, "cost_rub": "0.12"}
        )
    )
    monkeypatch.setattr(AgentAIPlannerPreviewView, "_generate", calls)

    def broken_delete(self, *args, **kwargs):
        raise RuntimeError("cleanup unavailable")

    monkeypatch.setattr(AgentPlanOperation, "delete", broken_delete)

    user = User.objects.create_user(
        username="planner-cleanup-failure",
        email="planner-cleanup-failure@example.test",
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/agents/ai-planner/preview/",
        {"description": "Build a supported workflow with safe approval steps"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["cost_rub"] == "0.12"
    assert AgentPlanOperation.objects.filter(owner=user, state="completed").count() == 1
