from __future__ import annotations

import asyncio
import base64
import json
import time
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import get_db
from app.routes import execution as execution_routes, agents as agent_routes
from app.schemas.agents import AgentCreateRequest
from app.schemas.execution import ExecutionRunCreateRequest
from app.services import agent_store, execution_run_store, execution_runner, waylo_connection
from app.services.waylo_target import WayloClient, WayloError
from app.services.waylo_livekit import run_waylo_call


WORKSPACE, AGENT = str(uuid4()), str(uuid4())
TARGET = {'id': 'temporary-mike', 'name': 'Mike', 'channel': 'voice', 'target': 'waylo',
          'connection': {'endpoint_url': 'https://api.poc.app.waylovoice.ai',
                         'workspace_id': WORKSPACE, 'waylo_agent_id': AGENT,
                         'auth_type': 'waylo_browser_session'}}
HEADERS = {'Origin': 'http://127.0.0.1:3012', 'X-CAE-Waylo-Control': '1',
           'Sec-Fetch-Site': 'same-origin'}


def jwt(seconds=600):
    body = base64.urlsafe_b64encode(json.dumps({'exp': time.time() + seconds}).encode()).decode().rstrip('=')
    return f'header.{body}.signature'


@pytest.fixture
def connected(tmp_path, monkeypatch):
    monkeypatch.setattr(waylo_connection, 'connection_store', waylo_connection.WayloConnectionStore(timer_factory=None))
    monkeypatch.setattr(agent_store, 'get_agent', lambda ident: deepcopy(TARGET) if ident == TARGET['id'] else None)
    monkeypatch.setattr(execution_runner, 'get_agent', agent_store.get_agent)
    monkeypatch.setattr(execution_run_store, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(execution_run_store, 'REPO_ROOT', tmp_path)
    execution_run_store.reset_execution_runs_for_tests()
    monkeypatch.setattr(execution_routes, 'resolve_execution_product_project_id', lambda **kw: None)
    monkeypatch.setattr(execution_routes, 'prepare_execution_reference_models', lambda payload: payload)
    app.dependency_overrides[get_db] = lambda: None
    token = jwt()
    proof, _ = waylo_connection.connect(token, TARGET['connection']['endpoint_url'], WORKSPACE, AGENT)
    yield token, proof, TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    waylo_connection.connection_store.clear()
    execution_run_store.reset_execution_runs_for_tests()


def run_payload(**extra):
    return {'agent_id': TARGET['id'], 'suite_id': 'waylo-mike-notes',
            'scenario_ids': ['mike-five-bags'], 'mode': 'pipecat_webrtc',
            'executor_id': 'waylo_livekit', **extra}


def test_temporary_schema_never_accepts_secret_ref_or_other_target():
    assert AgentCreateRequest.model_validate(TARGET).connection.secret_ref is None
    for change in ({'secret_ref': 'server-key'}, {'access_token': jwt()}):
        bad = deepcopy(TARGET)
        bad['connection'].update(change)
        with pytest.raises(ValueError):
            AgentCreateRequest.model_validate(bad)
    bad = deepcopy(TARGET)
    bad.update(target='http_endpoint', channel='text')
    with pytest.raises(ValueError):
        AgentCreateRequest.model_validate(bad)


@pytest.mark.parametrize('operation', ['run', 'capture', 'readiness'])
def test_existing_routes_require_browser_proof_and_local_control(connected, operation):
    _, proof, client = connected
    url = {'run': '/api/execution/runs', 'capture': f'/api/agents/{TARGET["id"]}/capture',
           'readiness': f'/api/agents/{TARGET["id"]}/readiness'}[operation]
    data = run_payload() if operation == 'run' else {'session_id': str(uuid4())}
    for headers, expected in (({}, 403), (HEADERS, 401), ({**HEADERS, 'X-CAE-Waylo-Session': 'wrong'}, 401),
                              ({**HEADERS, 'X-CAE-Waylo-Session': proof, 'Origin': 'https://evil.test'}, 403)):
        response = client.get(url, headers=headers) if operation == 'readiness' else client.post(url, json=data, headers=headers)
        assert response.status_code == expected
        assert proof not in response.text
    assert execution_run_store.list_execution_runs(user_id='execution-user') == []


def test_queue_authorization_is_memory_only_and_released(connected, monkeypatch, tmp_path):
    token, proof, client = connected
    checked = []
    def execute(run_id, payload):
        lease = waylo_connection.run_lease(run_id, TARGET)
        assert lease.token() == token
        checked.append(run_id)
        waylo_connection.release_run(run_id)
    monkeypatch.setattr(execution_routes, 'execute_execution_run', execute)
    response = client.post('/api/execution/runs', json=run_payload(),
                           headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == 200, response.text
    assert checked == [response.json()['execution_run_id']]
    serialized = response.text + ''.join(path.read_text() for path in tmp_path.rglob('*.json'))
    assert token not in serialized and proof not in serialized
    assert 'waylo_browser_session' in serialized
    with pytest.raises(waylo_connection.WayloConnectionError):
        waylo_connection.run_lease(checked[0], TARGET)


def test_queue_rejects_budget_and_disconnected_grants_without_persisting(connected):
    _, proof, client = connected
    response = client.post('/api/execution/runs', json=run_payload(iterations=10),
                           headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == 400 and 'expires' in response.text
    waylo_connection.disconnect(proof)
    assert client.post('/api/execution/runs', json=run_payload(),
                       headers={**HEADERS, 'X-CAE-Waylo-Session': proof}).status_code == 401
    assert execution_run_store.list_execution_runs(user_id='execution-user') == []


def test_disconnect_queue_binding_race_is_failed_not_orphaned(connected, monkeypatch):
    _, proof, client = connected
    def fail_bind(run_id, lease):
        waylo_connection.disconnect(proof)
        raise waylo_connection.WayloConnectionError('Waylo access expired.')
    monkeypatch.setattr(waylo_connection, 'bind_run', fail_bind)
    response = client.post('/api/execution/runs', json=run_payload(),
                           headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == 400
    runs = execution_run_store.list_execution_runs(user_id='execution-user')
    assert len(runs) == 1 and runs[0]['status'] == 'failed'


def test_http_retries_check_revocation_and_401_revokes(connected):
    _, proof, _ = connected
    lease = waylo_connection.authorize(proof, TARGET)
    calls = []
    def handler(request):
        calls.append(request)
        waylo_connection.disconnect(proof)
        return httpx.Response(503)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WayloClient(TARGET, client=http, credential_lease=lease)
            assert client._secret is None
            with pytest.raises(waylo_connection.WayloConnectionError):
                await client.request('GET', 'agents')
    asyncio.run(exercise())
    assert len(calls) == 1
    new_proof, _ = waylo_connection.connect(jwt(), TARGET['connection']['endpoint_url'], WORKSPACE, AGENT)
    rejected = waylo_connection.authorize(new_proof, TARGET)
    async def unauthorized():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401))) as http:
            client = WayloClient(TARGET, client=http, credential_lease=rejected)
            with pytest.raises(WayloError, match='revoked'):
                await client.request('GET', 'agents')
    asyncio.run(unauthorized())
    assert rejected.revoked.is_set()


def test_transport_rejects_scope_changes_and_discards_cookies(connected):
    _, proof, _ = connected
    lease = waylo_connection.authorize(proof, TARGET)
    for field, value in [('endpoint_url', 'https://evil.test'), ('workspace_id', str(uuid4())),
                         ('waylo_agent_id', str(uuid4()))]:
        changed = deepcopy(TARGET)
        changed['connection'][field] = value
        with pytest.raises(waylo_connection.WayloConnectionError):
            WayloClient(changed, credential_lease=lease)
    calls = []
    def handler(request):
        calls.append(request)
        assert 'cookie' not in request.headers
        return httpx.Response(200, json={}, headers={'Set-Cookie': 'refresh=not-to-be-reused; Path=/'})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            http.cookies.set('existing', 'ambient-cookie')
            client = WayloClient(TARGET, client=http, credential_lease=lease)
            await client.request('GET', 'agents')
            await client.request('GET', 'agents')
            assert not http.cookies
    asyncio.run(exercise())
    assert len(calls) == 2


def test_target_authentication_change_cannot_fall_back_to_server_key(connected, monkeypatch):
    _, proof, _ = connected
    lease = waylo_connection.authorize(proof, TARGET)
    changed = deepcopy(TARGET)
    changed['connection'].update(auth_type='bearer_secret', secret_ref='existing-server-key')
    monkeypatch.setattr(execution_runner, 'get_agent', lambda _: changed)
    monkeypatch.setattr(execution_runner, 'resolve_http_target_secret', lambda _: pytest.fail('No server fallback'))
    with pytest.raises(ValueError, match='authentication changed'):
        execution_runner.start_execution_run(ExecutionRunCreateRequest.model_validate(run_payload()), waylo_lease=lease)


def test_disconnect_cancels_active_rtc_and_closes_client(connected, monkeypatch, tmp_path):
    from app.services import waylo_livekit
    _, proof, _ = connected
    lease = waylo_connection.authorize(proof, TARGET)
    flags = []
    class Client:
        async def close(self):
            flags.append('client-closed')
    async def active_call(**kw):
        try:
            waylo_connection.disconnect(proof)
            await asyncio.sleep(10)
        finally:
            flags.append('rtc-closed')
    monkeypatch.setattr(waylo_livekit, '_run_waylo_call', active_call)
    async def exercise():
        with pytest.raises(waylo_connection.WayloConnectionError):
            await asyncio.wait_for(run_waylo_call(target=TARGET, correlation_id='test', scenario={},
                suite_id='waylo-mike-notes', artifact_dir=tmp_path, max_exchanges=1, timeout_seconds=30,
                next_utterance=None, synthesize=None, client=Client(), credential_lease=lease), 2)
    asyncio.run(exercise())
    assert flags == ['rtc-closed', 'client-closed']


def finished_waylo_run(connected, monkeypatch):
    from app.schemas.execution import ConversationRecord
    from app.services.waylo_target import project_capture
    from test_waylo_target import captured, S
    _, proof, http = connected
    monkeypatch.setattr(execution_routes, 'execute_execution_run', lambda *args: None)
    queued = http.post('/api/execution/runs', json=run_payload(), headers={**HEADERS, 'X-CAE-Waylo-Session': proof}).json()
    run_id, conv_id = queued['execution_run_id'], 'conv-refresh'
    native = captured(target_id=TARGET['id'], origin='cae_ai_tester')
    native['session']['metadata'] = {'cae': {'origin': 'cae_ai_tester', 'correlation_id': f'{run_id}/{conv_id}'}}
    export = project_capture(native, execution_run_id=run_id, conversation_id=conv_id)
    row = ConversationRecord(execution_run_id=run_id, conversation_id=conv_id,
        suite_id='waylo-mike-notes', scenario_id='mike-five-bags', mode='pipecat_webrtc',
        status='needs_review', ietf_vcon_export=export['vcon'], score=50,
        turns=[{'turn_index': 1, 'speaker': 'Caller', 'text': 'Actual tester spoken reference',
                'frame_metadata': {'source_text': 'Actual tester spoken reference', 'audio_sha256': 'sent-audio-hash'}},
               {'turn_index': 2, 'speaker': 'Agent', 'text': '[Target audio captured; transcript pending]',
                'frame_metadata': {'audio_sha256': 'received-audio-hash', 'transcript_source': 'livekit_remote_transcription'}}],
        live_events=[{'sequence': 1, 'kind': 'audio', 'speaker': 'Caller', 'text': 'Actual tester spoken reference',
                      'llm_output': 'Actual tester spoken reference', 'direction': 'tester_to_target',
                      'media_url': '/recorded-tester.wav', 'created_at': '2026-10-09T12:00:01Z'}],
        recording={'url': '/recorded-conversation.wav'},
        timeline=[{'label': 'Actual tester spoken reference', 't_ms': 0, 'kind': 'turn'}],
        audio_session={'session_id': S}, judge_reviews=[{'review_id': 'old', 'status': 'pending_confirmation'}])
    execution_run_store.upsert_conversation(run_id, row)
    execution_run_store.complete_execution_run(run_id, status='needs_review')
    return proof, http, run_id, conv_id, native


def test_late_provider_write_refresh_replaces_run_export_without_duplicate(connected, monkeypatch, tmp_path):
    from test_waylo_target import transcript, S
    from app.services.vcon_evidence import decode_evidence
    from app.services.assert_transcript_adapter import build_assert_inference_row
    proof, http, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    before = execution_run_store.get_conversation(run_id, conv_id)
    native['transcripts'].append(transcript(3, 'agent', 'Late durable notes readback'))
    native['items'] = [{'position': 1, 'rawText': 'White Potatoes', 'quantity': 5, 'unit': 'bags'}]
    async def read(self, session_id, *, origin='imported_human_call'):
        assert session_id == S and origin == 'cae_ai_tester'
        return deepcopy(native)
    monkeypatch.setattr(WayloClient, 'capture', read)
    data = {'session_id': S, 'execution_run_id': run_id, 'conversation_id': conv_id, 'user_id': 'execution-user'}
    for _ in range(2):
        response = http.post(f'/api/agents/{TARGET["id"]}/capture', json=data,
            headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
        assert response.status_code == 200, response.text
    after = execution_run_store.get_conversation(run_id, conv_id)
    assert after['ietf_vcon_export']['uuid'] == before['ietf_vcon_export']['uuid']
    assert len(after['ietf_vcon_export']['dialog']) == 3
    assert after['score'] is None and after['verdict'] == 'needs_review'
    assert after['judge_reviews'][0]['status'] == 'superseded'
    assert 'Late durable notes readback' in after['transcript']
    assert [(turn['turn_index'], turn['speaker'], turn['text']) for turn in after['turns']] == [
        (line['seq'], 'Caller' if line['role'] == 'user' else 'Agent', line['text']) for line in native['transcripts']]
    assert after['rtc_turns'] == before['turns']
    assert after['audio_session'] == before['audio_session']
    assert after['live_events'] == before['live_events']
    assert after['recording'] == before['recording']
    assert after['timeline'] == []
    assert all(turn['frame_metadata']['transcript_source'] == 'waylo_final_transcript' for turn in after['turns'])
    assert all('source_text' not in turn['frame_metadata'] and 'audio_sha256' not in turn['frame_metadata'] for turn in after['turns'])
    assert after['turns'][0]['frame_metadata']['text_source_label'] == 'Target ASR receipt (Waylo)'
    assert after['turns'][1]['frame_metadata']['text_source_label'] == 'Target-reported final transcript (Waylo)'
    assert all('llm_output' not in turn for turn in after['turns'])
    inference_row = build_assert_inference_row(run=execution_run_store.get_execution_run(run_id), conversation=after)
    messages = [event['edit']['message']['content'] for event in inference_row['events'] if 'message' in event['edit']]
    assert messages == [line['text'] for line in native['transcripts']]
    assert 'Actual tester spoken reference' not in messages
    assert after['final_state']['request_list'][0]['quantity'] == 5
    context = decode_evidence(after['ietf_vcon_export'])['context']
    assert context['source_call_kind'] == 'cae_ai_tester' and context['capture_completeness'] == 'provisional'
    assert context['execution_run_id'] == run_id and context['conversation_id'] == conv_id
    lines = (tmp_path / 'runs' / run_id / 'inference_set.jsonl').read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])['ietf_vcon_export'] == after['ietf_vcon_export']
    assert json.loads(lines[0])['turns'] == after['turns']
    assert json.loads(lines[0])['rtc_turns'] == before['turns']
    assert len(execution_run_store.get_execution_run(run_id)['conversations']) == 1


def test_changed_refreshes_keep_original_rtc_provenance_and_latest_assert_dialogue(connected, monkeypatch, tmp_path):
    from app.services.waylo_target import project_capture
    from app.services.assert_transcript_adapter import build_assert_inference_row
    from test_waylo_target import transcript
    _, _, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    original = execution_run_store.get_conversation(run_id, conv_id)
    native['transcripts'] = [transcript(1, text='Corrected provider caller receipt'),
                             transcript(2, 'agent', 'Final target wording')]
    for revision in range(2):
        expected = execution_run_store.get_conversation(run_id, conv_id)
        if revision:
            # ASR may split the caller differently and append a late target turn.
            native['transcripts'] = [transcript(1, text='Corrected provider'),
                                     transcript(2, text='caller receipt'),
                                     transcript(3, 'agent', 'Corrected target wording'),
                                     transcript(4, 'agent', 'Late readback')]
        export = project_capture(native, execution_run_id=run_id, conversation_id=conv_id)
        execution_run_store.refresh_waylo_evidence(run_id, expected, native, export)
        after = execution_run_store.get_conversation(run_id, conv_id)
        assert after['rtc_turns'] == original['turns']
        assert after['live_events'] == original['live_events']
        assert after['recording'] == original['recording']
        assert after['audio_session'] == original['audio_session']
        assert [turn['text'] for turn in after['turns']] == [line['text'] for line in native['transcripts']]
        inference = build_assert_inference_row(run=execution_run_store.get_execution_run(run_id), conversation=after)
        messages = [event['edit']['message']['content'] for event in inference['events'] if 'message' in event['edit']]
        assert messages == [line['text'] for line in native['transcripts']]
        assert all('source_text' not in turn['frame_metadata'] for turn in after['turns'])
        persisted = [json.loads(line) for line in (tmp_path / 'runs' / run_id / 'inference_set.jsonl').read_text().splitlines()]
        assert len(persisted) == 1 and persisted[0]['turns'] == after['turns']
        assert persisted[0]['rtc_turns'] == original['turns']


@pytest.mark.parametrize('receipts', [[], [('agent', 'Partial target transcript')],
    [('user', 'Partial caller transcript')], [('user', 'First fragment'), ('user', 'Second fragment')],
    [('user', '   '), ('agent', 'Target transcript')]])
def test_partial_refresh_refuses_atomically_without_replacing_saved_evidence(connected, monkeypatch, tmp_path, receipts):
    from app.schemas.execution import ConversationRecord
    from test_waylo_target import transcript
    proof, http, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    reviewed = {**execution_run_store.get_conversation(run_id, conv_id),
                'judge_reviews': [{'review_id': 'new-review', 'status': 'accepted'}],
                'score': 75, 'verdict': 'pass'}
    execution_run_store.upsert_conversation(run_id, ConversationRecord.model_validate(reviewed))
    before = execution_run_store.get_execution_run(run_id)
    run_path = tmp_path / 'runs' / run_id / 'run.json'
    run_bytes = run_path.read_bytes()
    inference_path = run_path.with_name('inference_set.jsonl')
    inference_path.write_text(json.dumps(reviewed) + '\n', encoding='utf-8')
    inference_bytes = inference_path.read_bytes()
    native['transcripts'] = [transcript(index, role, text) for index, (role, text) in enumerate(receipts, 1)]
    native['items'] = [{'position': 1, 'rawText': 'A new backend observation', 'quantity': 99, 'unit': 'boxes'}]
    async def read(*args, **kwargs):
        return deepcopy(native)
    monkeypatch.setattr(WayloClient, 'capture', read)
    response = http.post(f'/api/agents/{TARGET["id"]}/capture', json={
        'session_id': native['session']['id'], 'execution_run_id': run_id,
        'conversation_id': conv_id, 'user_id': 'execution-user'},
        headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == 400, response.text
    assert 'provisional transcript is shorter' in response.json()['detail']
    assert 'saved evidence and reviews were not changed' in response.json()['detail']
    assert execution_run_store.get_execution_run(run_id) == before
    assert execution_run_store.get_conversation(run_id, conv_id) == reviewed
    assert run_path.read_bytes() == run_bytes
    assert inference_path.read_bytes() == inference_bytes


@pytest.mark.parametrize('baseline_source', ['native', 'primary', 'rtc'])
def test_refresh_checks_each_saved_dialogue_baseline(connected, monkeypatch, baseline_source):
    from app.schemas.execution import ConversationRecord
    from app.services.waylo_target import project_capture
    from test_waylo_target import transcript
    _, _, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    original = execution_run_store.get_conversation(run_id, conv_id)
    if baseline_source == 'native':
        fuller = deepcopy(native)
        fuller['transcripts'].append(transcript(3, 'agent', 'Previously captured late response'))
        original['ietf_vcon_export'] = project_capture(fuller, execution_run_id=run_id, conversation_id=conv_id)['vcon']
    else:
        original['rtc_turns'] = deepcopy(original['turns'])
        key = 'turns' if baseline_source == 'primary' else 'rtc_turns'
        original[key].append({'turn_index': 3, 'speaker': 'Agent', 'text': '[Target audio captured; transcript pending]'})
    execution_run_store.upsert_conversation(run_id, ConversationRecord.model_validate(original))
    expected = execution_run_store.get_conversation(run_id, conv_id)
    before = execution_run_store.get_execution_run(run_id)
    with pytest.raises(ValueError, match='provisional transcript is shorter'):
        execution_run_store.refresh_waylo_evidence(run_id, expected, native,
            project_capture(native, execution_run_id=run_id, conversation_id=conv_id))
    assert execution_run_store.get_execution_run(run_id) == before


def test_later_provider_regression_cannot_erase_a_successful_refresh(connected, monkeypatch):
    from app.services.waylo_target import project_capture
    from test_waylo_target import transcript
    _, _, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    original = deepcopy(native)
    native['transcripts'].append(transcript(3, 'agent', 'Late target response'))
    expected = execution_run_store.get_conversation(run_id, conv_id)
    execution_run_store.refresh_waylo_evidence(run_id, expected, native,
        project_capture(native, execution_run_id=run_id, conversation_id=conv_id))
    expected = execution_run_store.get_conversation(run_id, conv_id)
    before = execution_run_store.get_execution_run(run_id)
    with pytest.raises(ValueError, match='provisional transcript is shorter'):
        execution_run_store.refresh_waylo_evidence(run_id, expected, original,
            project_capture(original, execution_run_id=run_id, conversation_id=conv_id))
    assert execution_run_store.get_execution_run(run_id) == before


def test_identical_partial_export_cannot_bypass_archived_rtc_guard(connected, monkeypatch):
    from app.schemas.execution import ConversationRecord
    from app.services.waylo_target import project_capture
    _, _, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    original = execution_run_store.get_conversation(run_id, conv_id)
    export = project_capture(native, execution_run_id=run_id, conversation_id=conv_id)
    execution_run_store.refresh_waylo_evidence(run_id, original, native, export)
    damaged = execution_run_store.get_conversation(run_id, conv_id)
    damaged['rtc_turns'].append({'turn_index': 3, 'speaker': 'Agent', 'text': '[Retained target audio; transcript pending]'})
    damaged['judge_reviews'] = [{'review_id': 'accepted-review', 'status': 'accepted'}]
    execution_run_store.upsert_conversation(run_id, ConversationRecord.model_validate(damaged))
    expected = execution_run_store.get_conversation(run_id, conv_id)
    before = execution_run_store.get_execution_run(run_id)
    with pytest.raises(ValueError, match='provisional transcript is shorter'):
        execution_run_store.refresh_waylo_evidence(run_id, expected, native, export)
    assert execution_run_store.get_execution_run(run_id) == before


def test_empty_native_baseline_still_preserves_nonempty_rtc_dialogue(connected, monkeypatch):
    from app.schemas.execution import ConversationRecord
    from app.services.waylo_target import project_capture
    _, _, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    native['transcripts'] = []
    export = project_capture(native, execution_run_id=run_id, conversation_id=conv_id)
    original = execution_run_store.get_conversation(run_id, conv_id)
    original['ietf_vcon_export'] = export['vcon']
    execution_run_store.upsert_conversation(run_id, ConversationRecord.model_validate(original))
    expected = execution_run_store.get_conversation(run_id, conv_id)
    before = execution_run_store.get_execution_run(run_id)
    with pytest.raises(ValueError, match='provisional transcript is shorter'):
        execution_run_store.refresh_waylo_evidence(run_id, expected, native, export)
    assert execution_run_store.get_execution_run(run_id) == before


def test_identical_empty_canonical_export_cannot_bypass_retained_rtc_guard(connected, monkeypatch):
    from app.schemas.execution import ConversationRecord
    from app.services.waylo_target import project_capture
    _, _, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    original = execution_run_store.get_conversation(run_id, conv_id)
    native['transcripts'] = []
    export = project_capture(native, execution_run_id=run_id, conversation_id=conv_id)
    damaged = {**original, 'turns': [], 'rtc_turns': deepcopy(original['turns']),
               'ietf_vcon_export': export['vcon'], 'transcript': '',
               'judge_reviews': [{'review_id': 'accepted-review', 'status': 'accepted'}]}
    execution_run_store.upsert_conversation(run_id, ConversationRecord.model_validate(damaged))
    expected = execution_run_store.get_conversation(run_id, conv_id)
    assert expected['turns'] == [] and expected['ietf_vcon_export'] == export['vcon']
    before = execution_run_store.get_execution_run(run_id)
    with pytest.raises(ValueError, match='provisional transcript is shorter'):
        execution_run_store.refresh_waylo_evidence(run_id, expected, native, export)
    assert execution_run_store.get_execution_run(run_id) == before


def test_empty_dialogue_can_refresh_backend_evidence_without_claiming_completeness(connected, monkeypatch):
    from app.schemas.execution import ConversationRecord
    from app.services.waylo_target import project_capture
    _, _, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    native['transcripts'] = []
    original = execution_run_store.get_conversation(run_id, conv_id)
    original.update(turns=[], live_events=[], ietf_vcon_export=project_capture(native,
        execution_run_id=run_id, conversation_id=conv_id)['vcon'])
    execution_run_store.upsert_conversation(run_id, ConversationRecord.model_validate(original))
    native['items'] = [{'position': 1, 'rawText': 'Observed backend note', 'quantity': 1}]
    expected = execution_run_store.get_conversation(run_id, conv_id)
    export = project_capture(native, execution_run_id=run_id, conversation_id=conv_id)
    execution_run_store.refresh_waylo_evidence(run_id, expected, native, export)
    after = execution_run_store.get_conversation(run_id, conv_id)
    assert after['turns'] == [] and after['rtc_turns'] == []
    assert after['final_state']['request_list'] == native['items']
    assert after['status'] == after['verdict'] == 'needs_review'
    from app.services.vcon_evidence import decode_evidence
    assert decode_evidence(after['ietf_vcon_export'])['context']['capture_completeness'] == 'provisional'


def test_identical_export_refresh_repairs_original_rtc_turns_once(connected, monkeypatch):
    from app.services.waylo_target import project_capture
    from app.services.assert_transcript_adapter import build_assert_inference_row
    from app.schemas.execution import ConversationRecord
    _, _, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    original = execution_run_store.get_conversation(run_id, conv_id)
    export = project_capture(native, execution_run_id=run_id, conversation_id=conv_id)
    assert export['vcon'] == original['ietf_vcon_export']
    execution_run_store.refresh_waylo_evidence(run_id, original, native, export)
    after = execution_run_store.get_conversation(run_id, conv_id)
    assert after['turns'] != original['turns']
    assert after['rtc_turns'] == original['turns']
    assert after['judge_reviews'][0]['status'] == 'superseded' and after['score'] is None
    inference = build_assert_inference_row(run=execution_run_store.get_execution_run(run_id), conversation=after)
    assert [event['edit']['message']['content'] for event in inference['events'] if 'message' in event['edit']] == [
        line['text'] for line in native['transcripts']]
    reviewed = {**after, 'judge_reviews': [{'review_id': 'fresh-review', 'status': 'accepted'}],
                'score': 75, 'verdict': 'pass'}
    execution_run_store.upsert_conversation(run_id, ConversationRecord.model_validate(reviewed))
    expected = execution_run_store.get_conversation(run_id, conv_id)
    execution_run_store.refresh_waylo_evidence(run_id, expected, native, export)
    assert execution_run_store.get_conversation(run_id, conv_id) == expected


@pytest.mark.parametrize('status,error', [('failed', 'Target response timeout'), ('failed', None),
    ('needs_review', 'Caller audio send failed')])
def test_refresh_preserves_failed_execution_and_verdict(connected, monkeypatch, tmp_path, status, error):
    from app.schemas.execution import ConversationRecord
    from test_waylo_target import transcript, S
    proof, http, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    before = execution_run_store.get_conversation(run_id, conv_id)
    failure = error or 'RTC failed without an error string'
    execution_run_store.upsert_conversation(run_id, ConversationRecord.model_validate({
        **before, 'status': status, 'verdict': 'failed', 'error': error,
        'final_state': {'tester_error': failure},
    }))
    execution_run_store.complete_execution_run(run_id, status='failed')
    assert execution_run_store.get_execution_run(run_id)['error'] is None
    native['transcripts'].append(transcript(3, 'agent', 'Late transcript after RTC failure'))
    async def read(*args, **kwargs):
        return deepcopy(native)
    monkeypatch.setattr(WayloClient, 'capture', read)
    data = {'session_id': S, 'execution_run_id': run_id, 'conversation_id': conv_id, 'user_id': 'execution-user'}
    for _ in range(2):
        response = http.post(f'/api/agents/{TARGET["id"]}/capture', json=data,
            headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
        assert response.status_code == 200, response.text
    after = execution_run_store.get_conversation(run_id, conv_id)
    assert after['status'] == after['verdict'] == 'failed'
    assert after['error'] == error and after['final_state']['tester_error'] == failure
    assert after['score'] is None and after['judge_reviews'][0]['status'] == 'superseded'
    assert 'Late transcript after RTC failure' in after['transcript']
    assert execution_run_store.get_execution_run(run_id)['status'] == 'failed'
    lines = (tmp_path / 'runs' / run_id / 'inference_set.jsonl').read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])['status'] == json.loads(lines[0])['verdict'] == 'failed'


def test_refresh_retains_run_failure_from_another_conversation(connected, monkeypatch, tmp_path):
    from app.schemas.execution import ConversationRecord
    from test_waylo_target import transcript, S
    proof, http, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    before = execution_run_store.get_conversation(run_id, conv_id)
    other = ConversationRecord.model_validate({**before, 'conversation_id': 'conv-failed-other',
        'iteration': 2, 'status': 'failed', 'verdict': 'failed', 'score': None, 'error': None})
    execution_run_store.upsert_conversation(run_id, other)
    execution_run_store.complete_execution_run(run_id, status='failed')
    assert execution_run_store.get_execution_run(run_id)['error'] is None
    native['transcripts'].append(transcript(3, 'agent', 'Late transcript for successful capture'))
    async def read(*args, **kwargs):
        return deepcopy(native)
    monkeypatch.setattr(WayloClient, 'capture', read)
    response = http.post(f'/api/agents/{TARGET["id"]}/capture', json={
        'session_id': S, 'execution_run_id': run_id, 'conversation_id': conv_id, 'user_id': 'execution-user'},
        headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == 200, response.text
    after = execution_run_store.get_conversation(run_id, conv_id)
    assert after['status'] == after['verdict'] == 'needs_review'
    assert after['score'] is None
    assert execution_run_store.get_conversation(run_id, other.conversation_id) == other.model_dump(mode='json')
    assert execution_run_store.get_execution_run(run_id)['status'] == 'failed'
    lines = (tmp_path / 'runs' / run_id / 'inference_set.jsonl').read_text().splitlines()
    assert len(lines) == 2
    assert {json.loads(line)['conversation_id']: json.loads(line)['status'] for line in lines} == {
        conv_id: 'needs_review', other.conversation_id: 'failed'}


@pytest.mark.parametrize('change,expected', [({'user_id': 'wrong'}, 404),
    ({'conversation_id': 'other'}, 404), ({'session_id': str(uuid4())}, 409)])
def test_refresh_rejects_cross_run_or_session_identity_before_provider_read(connected, monkeypatch, change, expected):
    from test_waylo_target import S
    proof, http, run_id, conv_id, _ = finished_waylo_run(connected, monkeypatch)
    async def no_read(*args, **kwargs):
        pytest.fail('Wrong refresh identity must not read the provider')
    monkeypatch.setattr(WayloClient, 'capture', no_read)
    data = {'session_id': S, 'execution_run_id': run_id, 'conversation_id': conv_id, 'user_id': 'execution-user', **change}
    response = http.post(f'/api/agents/{TARGET["id"]}/capture', json=data,
        headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == expected


def test_refresh_cannot_relabel_foreign_correlation_or_import_as_human(connected, monkeypatch):
    from test_waylo_target import S
    proof, http, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    native['session']['metadata']['cae']['correlation_id'] = 'another/run'
    async def read(*args, **kwargs):
        return deepcopy(native)
    monkeypatch.setattr(WayloClient, 'capture', read)
    for data in ({'session_id': S}, {'session_id': S, 'execution_run_id': run_id, 'conversation_id': conv_id, 'user_id': 'execution-user'}):
        response = http.post(f'/api/agents/{TARGET["id"]}/capture', json=data,
            headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
        assert response.status_code == 409


def test_refresh_checks_concurrent_evidence_change(connected, monkeypatch):
    from app.services.waylo_target import project_capture
    proof, http, run_id, conv_id, native = finished_waylo_run(connected, monkeypatch)
    expected = execution_run_store.get_conversation(run_id, conv_id)
    from app.schemas.execution import ConversationRecord
    execution_run_store.upsert_conversation(run_id, ConversationRecord.model_validate({**expected, 'score': 75}))
    with pytest.raises(ValueError, match='changed during refresh'):
        execution_run_store.refresh_waylo_evidence(run_id, expected, native, project_capture(native))
