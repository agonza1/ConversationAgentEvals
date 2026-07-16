from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import agent_store, execution_run_store
from app.services.execution_runner import execute_execution_run, start_execution_run
from app.schemas.execution import ExecutionRunCreateRequest


client = TestClient(app)


def setup_function() -> None:
    execution_run_store.reset_execution_runs_for_tests()
    agent_store.reset_agents_for_tests(clear_files=True)


def test_agent_crud_round_trip():
    listed = client.get('/api/agents')
    assert listed.status_code == 200
    agents = listed.json()['agents']
    assert {item['id'] for item in agents} >= {'mock-text-agent', 'acc-voice-fixture-agent'}

    created = client.post(
        '/api/agents',
        json={
            'name': 'Custom support bot',
            'channel': 'text',
            'target': 'mock_agent',
            'description': 'Test agent',
        },
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()['id']

    detail = client.get(f'/api/agents/{agent_id}')
    assert detail.status_code == 200
    assert detail.json()['name'] == 'Custom support bot'

    patched = client.patch(f'/api/agents/{agent_id}', json={'name': 'Renamed bot'})
    assert patched.status_code == 200
    assert patched.json()['name'] == 'Renamed bot'

    deleted = client.delete(f'/api/agents/{agent_id}')
    assert deleted.status_code == 200
    assert client.get(f'/api/agents/{agent_id}').status_code == 404


def test_execution_with_agent_id_and_metrics_summary():
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            agent_id='mock-text-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )
    assert queued['agent_id'] == 'mock-text-agent'
    assert queued['mode'] == 'text_callable'

    finished = execute_execution_run(
        queued['execution_run_id'],
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            agent_id='mock-text-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        ),
    )
    assert finished['status'] in {'completed', 'needs_review'}
    conversation = finished['conversations'][0]
    assert conversation['metrics_summary'] is not None
    assert 'turn_count' in conversation['metrics_summary']
    assert isinstance(conversation.get('timeline'), list)
    assert finished.get('run_snapshot_path')

    execution_run_store.reset_execution_runs_for_tests()
    reloaded = execution_run_store.get_execution_run(queued['execution_run_id'])
    assert reloaded is not None
    assert reloaded['execution_run_id'] == queued['execution_run_id']
    assert reloaded['conversations'][0]['metrics_summary']['turn_count'] >= 1


def test_voice_fixture_agent_run_includes_latency_metrics():
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id='acc-voice-fixture-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )
    assert queued['mode'] == 'voice_fixture'
    finished = execute_execution_run(
        queued['execution_run_id'],
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id='acc-voice-fixture-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        ),
    )
    assert finished['status'] in {'completed', 'needs_review', 'failed'}
    conversation = finished['conversations'][0]
    if conversation['status'] == 'completed':
        assert conversation['metrics_summary']['latency']['count'] >= 0
        assert isinstance(conversation['timeline'], list)


def test_execution_persists_model_name_default_and_override():
    queued_default = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            agent_id='mock-text-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )
    assert queued_default['model_name'] == 'gpt-5.4'

    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            agent_id='mock-text-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
            model_name='gpt-5.4-mini',
        )
    )
    assert queued['model_name'] == 'gpt-5.4-mini'

    via_api = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'agent_id': 'mock-text-agent',
            'user_id': 'agent-runs-user',
            'project_id': 'agent-runs-project',
            'model_name': 'gpt-4.1',
            'iterations': 1,
        },
    )
    assert via_api.status_code == 200, via_api.text
    assert via_api.json()['model_name'] == 'gpt-4.1'


def test_rejects_unsafe_agent_id_and_seed_delete():
    bad = client.post(
        '/api/agents',
        json={'id': '../evil', 'name': 'Evil', 'channel': 'text', 'target': 'mock_agent'},
    )
    assert bad.status_code == 400

    seed_delete = client.delete('/api/agents/mock-text-agent')
    assert seed_delete.status_code == 400
    assert 'Seed agent' in seed_delete.json()['detail']


def test_rejects_null_patch_on_non_nullable_fields():
    created = client.post(
        '/api/agents',
        json={'name': 'Nullable guard', 'channel': 'text', 'target': 'mock_agent', 'description': 'keep'},
    )
    assert created.status_code == 200
    agent_id = created.json()['id']

    null_name = client.patch(f'/api/agents/{agent_id}', json={'name': None})
    assert null_name.status_code == 400

    cleared = client.patch(f'/api/agents/{agent_id}', json={'description': None})
    assert cleared.status_code == 200
    assert cleared.json()['description'] is None


def test_text_offline_acc_fixture_stays_text_callable():
    from app.services.execution_runner import _resolve_agent_payload

    created = client.post(
        '/api/agents',
        json={
            'name': 'Offline text fixture',
            'channel': 'text',
            'target': 'offline_acc_fixture',
        },
    )
    assert created.status_code == 200
    agent_id = created.json()['id']

    resolved = _resolve_agent_payload(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id=agent_id,
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )
    assert resolved.mode == 'text_callable'
    assert resolved.text_callable == 'offline_acc_fixture'

    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id=agent_id,
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )
    assert queued['mode'] == 'text_callable'
