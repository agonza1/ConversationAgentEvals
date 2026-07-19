from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.execution import ConversationRecord, ExecutionRunCreateRequest, LiveExecutionEvent
from app.services import execution_run_store
from app.services.execution_run_store import reset_execution_runs_for_tests
from app.services.execution_runner import start_execution_run


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
    assert [event['speaker'].lower() for event in conversation['live_events']] == [
        turn['speaker'].lower() for turn in conversation['turns']
    ]
    assert all(event['kind'] == 'message' for event in conversation['live_events'])
    assert conversation['verdict'] in {'pass', 'needs_review'}
    assert completed['inference_set_path']
    inference_path = Path(completed['inference_set_path'])
    if not inference_path.is_absolute():
        inference_path = Path(__file__).resolve().parents[3] / inference_path
    assert inference_path.is_file()
    lines = [line for line in inference_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1


def test_live_audio_segment_requires_run_owner_and_observed_event():
    queued = start_execution_run(ExecutionRunCreateRequest(
        suite_id='call-center-voice-ai',
        scenario_ids=['cancellation-rescue'],
        user_id='audio-owner',
        project_id='audio-project',
    ))
    run_id = queued['execution_run_id']
    conversation_id = f'{run_id}-cancellation-rescue-1'
    execution_run_store.upsert_conversation(run_id, ConversationRecord(
        conversation_id=conversation_id,
        execution_run_id=run_id,
        suite_id='call-center-voice-ai',
        scenario_id='cancellation-rescue',
        mode='pipecat_webrtc',
        status='running',
    ))
    execution_run_store.append_live_event(run_id, conversation_id, LiveExecutionEvent(
        sequence=1,
        kind='audio',
        speaker='Caller',
        text='Current-run caller audio.',
        media_url=f'/api/execution/runs/{run_id}/conversations/{conversation_id}/audio/1?user_id=audio-owner',
        mime_type='audio/wav',
        created_at='2026-07-18T20:00:00+00:00',
    ))
    live_dir = execution_run_store.RUNS_DIR / run_id / 'audio' / 'live'
    live_dir.mkdir(parents=True)
    payload = b'RIFF-current-run-wav'
    (live_dir / f'{conversation_id}-1.wav').write_bytes(payload)

    allowed = client.get(
        f'/api/execution/runs/{run_id}/conversations/{conversation_id}/audio/1',
        params={'user_id': 'audio-owner'},
    )
    assert allowed.status_code == 200
    assert allowed.content == payload
    assert allowed.headers['content-type'].startswith('audio/wav')
    denied = client.get(
        f'/api/execution/runs/{run_id}/conversations/{conversation_id}/audio/1',
        params={'user_id': 'someone-else'},
    )
    assert denied.status_code == 404


def test_execution_listener_token_is_receive_only_owner_scoped_and_ephemeral():
    import app.routes.execution as execution_routes

    queued = start_execution_run(ExecutionRunCreateRequest(
        suite_id='call-center-voice-ai',
        scenario_ids=['cancellation-rescue'],
        user_id='listener-owner',
        project_id='listener-project',
    ))
    run_id = queued['execution_run_id']
    conversation_id = f'{run_id}-cancellation-rescue-1'
    execution_run_store.upsert_conversation(run_id, ConversationRecord(
        conversation_id=conversation_id,
        execution_run_id=run_id,
        suite_id='call-center-voice-ai',
        scenario_id='cancellation-rescue',
        mode='pipecat_webrtc',
        status='running',
    ))
    execution_run_store.append_live_event(run_id, conversation_id, LiveExecutionEvent(
        sequence=1,
        kind='audio',
        speaker='Agent',
        text='Current-run target audio.',
        media_url=f'/api/execution/runs/{run_id}/conversations/{conversation_id}/audio/1?user_id=listener-owner',
        mime_type='audio/wav',
        created_at='2026-07-18T20:00:00+00:00',
    ))
    live_dir = execution_run_store.RUNS_DIR / run_id / 'audio' / 'live'
    live_dir.mkdir(parents=True, exist_ok=True)
    payload = b'RIFF-listener-current-run-wav'
    (live_dir / f'{conversation_id}-1.wav').write_bytes(payload)

    denied = client.post(
        f'/api/execution/runs/{run_id}/listener-token',
        params={'user_id': 'someone-else'},
    )
    assert denied.status_code == 404

    issued = client.post(
        f'/api/execution/runs/{run_id}/listener-token',
        params={'user_id': 'listener-owner'},
        json={'ttl_seconds': 120},
    )
    assert issued.status_code == 200, issued.text
    listener = issued.json()['listener']
    assert listener['read_only'] is True
    assert listener['can_inject_audio'] is False
    assert listener['requires_microphone'] is False
    token = listener['token']

    state = client.get(f'/api/execution/listeners/{token}')
    assert state.status_code == 200
    assert state.json()['listener']['read_only'] is True
    assert state.json()['listener']['can_inject_audio'] is False
    assert state.json()['conversations'][0]['live_events'][0]['speaker'] == 'Agent'

    blocked_write = client.post(f'/api/execution/listeners/{token}')
    assert blocked_write.status_code == 405

    audio = client.get(f'/api/execution/listeners/{token}/conversations/{conversation_id}/audio/1')
    assert audio.status_code == 200
    assert audio.content == payload

    execution_routes._LISTENER_TOKENS[token]['expires_at'] = datetime.now(UTC) - timedelta(seconds=1)
    expired = client.get(f'/api/execution/listeners/{token}')
    assert expired.status_code == 403


def test_failed_conversation_is_preserved_in_inference_set(monkeypatch):
    def fail_callable(*_args, **_kwargs):
        raise RuntimeError('simulated provider disconnect')

    monkeypatch.setattr('app.services.execution_runner._execute_text_callable', fail_callable)
    queued = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'mode': 'text_callable',
            'text_callable': 'mock_agent',
            'iterations': 1,
            'user_id': 'failed-evidence-user',
            'project_id': 'failed-evidence-project',
        },
    )
    assert queued.status_code == 200, queued.text

    completed = _wait_for_terminal(queued.json()['execution_run_id'], user_id='failed-evidence-user')
    assert completed['status'] == 'failed'
    assert completed['conversations'][0]['status'] == 'failed'
    inference_path = Path(completed['inference_set_path'])
    if not inference_path.is_absolute():
        inference_path = Path(__file__).resolve().parents[3] / inference_path
    rows = [json.loads(line) for line in inference_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]['status'] == 'failed'
    assert 'simulated provider disconnect' in rows[0]['error']


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


def test_pipecat_reference_rejects_scenario_without_matching_tester_plan():
    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'mode': 'pipecat_webrtc',
            'user_id': 'exec-user',
            'project_id': 'exec-project',
        },
    )
    assert response.status_code == 400
    assert response.json()['detail'] == (
        'pipecat_webrtc currently supports only scenario cancellation-rescue; '
        'its tester act plan is scenario-specific.'
    )


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
