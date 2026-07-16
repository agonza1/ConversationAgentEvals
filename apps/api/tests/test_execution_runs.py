from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.execution_run_store import reset_execution_runs_for_tests


client = TestClient(app)


def setup_function() -> None:
    reset_execution_runs_for_tests()


def test_text_callable_execution_appends_conversations_and_writes_inference_set():
    queued = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'mode': 'text_callable',
            'text_callable': 'mock_agent',
            'iterations': 1,
            'user_id': 'exec-user',
            'project_id': 'exec-project',
        },
    )
    assert queued.status_code == 200, queued.text
    run_id = queued.json()['execution_run_id']
    assert queued.json()['status'] == 'queued'
    assert queued.json()['progress']['total_conversations'] == 1

    completed = _wait_for_terminal(run_id, user_id='exec-user')
    assert completed['status'] in {'completed', 'needs_review'}
    assert len(completed['conversations']) == 1
    conversation = completed['conversations'][0]
    assert conversation['status'] == 'completed'
    assert conversation['scenario_id'] == 'billing-address-change'
    assert conversation['turns']
    assert conversation['transcript']
    assert conversation['verdict'] in {'pass', 'needs_review'}
    assert completed['inference_set_path']
    inference_path = Path(completed['inference_set_path'])
    if not inference_path.is_absolute():
        inference_path = Path(__file__).resolve().parents[3] / inference_path
    assert inference_path.is_file()
    lines = [line for line in inference_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1


def test_voice_fixture_execution_runs_audio_plan_and_lists_mid_run():
    queued = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'mode': 'voice_fixture',
            'iterations': 1,
            'user_id': 'voice-user',
            'project_id': 'voice-project',
            'evaluate': True,
        },
    )
    assert queued.status_code == 200, queued.text
    run_id = queued.json()['execution_run_id']

    completed = _wait_for_terminal(run_id, user_id='voice-user')
    assert completed['status'] in {'completed', 'needs_review', 'failed'}
    assert completed['mode'] == 'voice_fixture'
    assert len(completed['conversations']) == 1
    conversation = completed['conversations'][0]
    assert conversation['scenario_id'] == 'cancellation-rescue'
    assert conversation['turns']
    assert any(turn.get('act_id') for turn in conversation['turns'])
    listed = client.get('/api/execution/runs', params={'user_id': 'voice-user', 'project_id': 'voice-project'})
    assert listed.status_code == 200
    assert any(item['execution_run_id'] == run_id for item in listed.json())


def test_text_suite_execution_writes_multiple_inference_rows():
    queued = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change', 'angry-outage-escalation'],
            'mode': 'text_callable',
            'text_callable': 'mock_agent',
            'iterations': 1,
            'user_id': 'suite-exec-user',
            'project_id': 'suite-exec-project',
        },
    )
    assert queued.status_code == 200, queued.text
    run_id = queued.json()['execution_run_id']
    assert queued.json()['progress']['total_conversations'] == 2
    completed = _wait_for_terminal(run_id, user_id='suite-exec-user')
    assert len(completed['conversations']) == 2
    assert completed['progress']['completed_conversations'] == 2
    inference_path = Path(completed['inference_set_path'])
    if not inference_path.is_absolute():
        inference_path = Path(__file__).resolve().parents[3] / inference_path
    lines = [line for line in inference_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 2


def test_execution_run_rejects_unknown_scenario():
    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['does-not-exist'],
            'mode': 'text_callable',
            'user_id': 'exec-user',
            'project_id': 'exec-project',
        },
    )
    assert response.status_code == 400


def test_execution_run_rejects_unsupported_text_callable_before_queueing():
    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'mode': 'text_callable',
            'text_callable': 'not-a-supported-callable',
            'user_id': 'exec-user',
            'project_id': 'exec-project',
        },
    )
    assert response.status_code == 422


def test_execution_run_rejects_duplicate_scenario_ids():
    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change', 'billing-address-change'],
            'mode': 'text_callable',
            'user_id': 'exec-user',
            'project_id': 'exec-project',
        },
    )
    assert response.status_code == 400
    assert 'Duplicate' in response.json()['detail']


def test_voice_fixture_rejects_non_cancellation_scenario():
    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'mode': 'voice_fixture',
            'user_id': 'exec-user',
            'project_id': 'exec-project',
        },
    )
    assert response.status_code == 400
    assert 'cancellation-rescue' in response.json()['detail']


def test_offline_acc_fixture_rejects_non_cancellation_scenario():
    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'mode': 'text_callable',
            'text_callable': 'offline_acc_fixture',
            'user_id': 'exec-user',
            'project_id': 'exec-project',
        },
    )
    assert response.status_code == 400


def test_mock_agent_evaluate_false_omits_verdict():
    queued = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'mode': 'text_callable',
            'text_callable': 'mock_agent',
            'evaluate': False,
            'user_id': 'capture-user',
            'project_id': 'capture-project',
        },
    )
    assert queued.status_code == 200, queued.text
    completed = _wait_for_terminal(queued.json()['execution_run_id'], user_id='capture-user')
    conversation = completed['conversations'][0]
    assert conversation['status'] == 'completed'
    assert conversation['transcript']
    assert conversation.get('verdict') is None
    assert conversation.get('score') is None


def test_execution_rejects_fixture_path_outside_allowlist(tmp_path: Path):
    outside = tmp_path / 'secrets.json'
    outside.write_text('{"dialog":[]}', encoding='utf-8')
    queued = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'mode': 'text_callable',
            'text_callable': 'offline_acc_fixture',
            'voice_fixture_path': str(outside),
            'evaluate': False,
            'user_id': 'path-user',
            'project_id': 'path-project',
        },
    )
    assert queued.status_code == 400
    assert 'docs/examples' in queued.json()['detail']


def _wait_for_terminal(run_id: str, *, user_id: str, timeout_seconds: float = 20.0) -> dict:
    deadline = time.time() + timeout_seconds
    latest = {}
    while time.time() < deadline:
        response = client.get(f'/api/execution/runs/{run_id}', params={'user_id': user_id})
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest.get('status') in {'completed', 'needs_review', 'failed'}:
            return latest
        time.sleep(0.05)
    raise AssertionError(f'execution run did not finish: {latest}')
