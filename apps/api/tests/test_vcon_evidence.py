from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.benchmark_service import get_suite, run_scenario
from app.services.execution_vcon import build_ietf_execution_vcon, validate_ietf_vcon
from app.services.vcon_evidence import (
    PURPOSE, attach_evidence, build_benchmark_vcon, decode_evidence, evidence_body, intake_vcon,
    latest_tool_events,
)


def sample():
    scenario = get_suite('call-center-voice-ai')['scenarios'][0]
    return {'suite_id': 'call-center-voice-ai', 'scenario_id': scenario['id'],
            'transcript': scenario['sample_transcript'], 'action_trace': scenario['sample_action_trace'],
            'final_state': scenario['sample_final_state']}


def test_vcon_only_roundtrip_reproduces_scores_and_verdict():
    payload = sample()
    original = run_scenario(payload, persist_artifacts=False)
    replay = run_scenario({'vcon': original['ietf_vcon_export']}, persist_artifacts=False)
    for key in ('overall_score', 'verdict', 'task_completion_score', 'final_state_score',
                'completed_actions', 'missing_actions', 'score_components'):
        assert replay.get(key) == original.get(key), key
    assert replay['vcon_intake_summary']['tool_events'] == len(payload['action_trace'])
    assert replay['vcon_intake_summary']['state_snapshots'] == 1


@pytest.mark.parametrize('status', ['requested', 'running', 'timeout', 'error', 'cancelled', 'unknown'])
def test_incomplete_tool_call_cannot_be_counted_as_completed(status):
    payload = sample()
    payload['action_trace'][0]['status'] = status
    vcon = build_benchmark_vcon(payload, payload['transcript'])
    report = run_scenario({'vcon': vcon}, persist_artifacts=False)
    assert report['verdict'] == 'needs_review'
    assert payload['action_trace'][0]['action'] in report['missing_actions']


def test_historical_pass_does_not_override_failed_tool_and_state():
    payload = sample()
    payload['action_trace'][0]['status'] = 'failed'
    payload['final_state'] = {'complete': False}
    vcon = build_benchmark_vcon(payload, payload['transcript'])
    vcon['analysis'].append({'type': 'evaluation', 'body': {'score': 100, 'verdict': 'pass'}})
    assert run_scenario({'vcon': vcon}, persist_artifacts=False)['verdict'] == 'needs_review'


def test_transcript_only_has_no_business_evidence():
    payload = sample()
    vcon = build_benchmark_vcon({'suite_id': payload['suite_id'], 'scenario_id': payload['scenario_id']}, payload['transcript'])
    replay = run_scenario({'vcon': vcon}, persist_artifacts=False)
    assert replay['task_completion_score'] is None
    assert replay['final_state_score'] is None
    assert replay['vcon_intake_summary']['evidence_level'] == 'transcript_only'


def test_conflicting_evidence_and_contract_fail_closed():
    payload = sample()
    report = run_scenario(payload, persist_artifacts=False)
    vcon = report['ietf_vcon_export']
    with pytest.raises(ValueError, match='Conflicting final_state'):
        intake_vcon({'vcon': vcon, 'final_state': {'complete': False}})
    with pytest.raises(ValueError, match='Conflicting transcript'):
        intake_vcon({'vcon': vcon, 'transcript': 'Agent: canceled!'})
    with pytest.raises(ValueError, match='Conflicting scenario_id'):
        intake_vcon({'vcon': vcon, 'scenario_id': 'other'})
    bad = deepcopy(vcon)
    bad['attachments'][0]['body']['context']['scenario_contract_sha256'] = 'different'
    with pytest.raises(ValueError, match='contract differs'):
        run_scenario({'vcon': bad}, persist_artifacts=False)


@pytest.mark.parametrize('mutation', ['version', 'duplicate', 'reference', 'state'])
def test_malformed_profile_is_rejected(mutation):
    payload = sample()
    vcon = build_benchmark_vcon(payload, payload['transcript'])
    body = vcon['attachments'][0]['body']
    if mutation == 'version':
        body['schema'] = 'cae-execution-evidence-v999'
    elif mutation == 'duplicate':
        body['tool_events'].append(deepcopy(body['tool_events'][0]))
    elif mutation == 'reference':
        body['tool_events'][0]['dialog'] = 9999
    else:
        body['state_snapshots'].append(deepcopy(body['state_snapshots'][0]))
    with pytest.raises(ValueError):
        intake_vcon({'vcon': vcon})


def test_profile_preserves_unknown_content_and_redacts_credentials():
    payload = sample()
    payload['action_trace'][0]['arguments'] = {'account_id': '123', 'api_key': 'secret'}
    payload['final_state']['business_state'] = {'subscription': 'active', 'authorization': 'secret'}
    vcon = build_benchmark_vcon(payload, payload['transcript'], synthetic=True)
    body = decode_evidence(vcon)
    assert body['synthetic'] is True
    assert body['tool_events'][0]['arguments'] == {'account_id': '123'}
    assert body['state_snapshots'][0]['state']['business_state'] == {'subscription': 'active'}
    assert len(body['redactions']) == 2
    unknown = {'purpose': 'Other system', 'body': {'custom': [1, 2]}}
    vcon['attachments'].append(unknown)
    vcon['analysis'].append({'type': 'custom', 'body': {'retained': True}})
    again = attach_evidence(vcon, body)
    assert again['attachments'][-1] == unknown
    assert again['analysis'][-1] == vcon['analysis'][-1]
    assert len([a for a in again['attachments'] if a.get('purpose') == PURPOSE]) == 1


def test_capture_does_not_claim_browser_playout_or_business_state():
    vcon = build_ietf_execution_vcon(
        conversation_id='c', execution_run_id='e', suite_id='s', scenario_id='x', scenario_title=None,
        mode='pipecat_webrtc', turns=[{'text': 'Hello', 'speaker': 'agent', 'frame_metadata': {'source_text': 'Hello', 'asr_receipt': 'Hi'}}],
        recording=None, created_at='2026-09-30T12:00:00Z', updated_at='2026-09-30T12:01:00Z',
        live_events=[{'sequence': 1, 'kind': 'audio', 'created_at': '2026-09-30T12:00:10Z'}],
        action_trace=[{'name': 'lookup', 'status': 'success', 'call_id': 'tool-1'}],
        final_state={'outcome': 'reference_conversation_captured', 'complete': True},
    )
    body = decode_evidence(vcon)
    assert body['state_snapshots'] == []
    assert body['tool_events'][0]['call_id'] == 'tool-1'
    assert 'audio.available' in [e['event_type'] for e in body['voice_events']]
    assert 'audio.played' not in [e['event_type'] for e in body['voice_events']]


def test_full_sample_and_intake_api():
    client = TestClient(app)
    response = client.get('/api/benchmarks/evidence/sample-vcon', params={'suite_id': 'call-center-voice-ai', 'scenario_id': 'billing-address-change'})
    assert response.status_code == 200
    vcon = response.json()
    assert vcon['vcon'] == '0.4.0'
    response = client.post('/api/benchmarks/evidence/intake', json={'vcon': vcon})
    assert response.status_code == 200
    assert response.json()['summary']['synthetic'] is True
    assert response.json()['evidence']['final_state']
    vcon['attachments'][0]['body']['schema'] = 'invalid'
    assert client.post('/api/benchmarks/evidence/intake', json={'vcon': vcon}).status_code == 422


def test_lifecycle_reducer_uses_capture_sequence_not_array_order():
    payload = sample()
    payload['action_trace'] = [
        {'event_id': 'result', 'sequence': 2, 'call_id': 'call', 'name': 'lookup', 'status': 'success'},
        {'event_id': 'request', 'sequence': 1, 'call_id': 'call', 'name': 'lookup', 'status': 'success', 'event_type': 'tool_call.requested'},
    ]
    vcon = build_benchmark_vcon(payload, payload['transcript'])
    loaded, _ = intake_vcon({'vcon': vcon})
    assert len(decode_evidence(vcon)['tool_events']) == 2
    assert loaded['action_trace'][0]['event_id'] == 'result'
    assert loaded['action_trace'][0]['status'] == 'success'
    with pytest.raises(ValueError, match='Conflicting scenario_id'):
        intake_vcon({'vcon': vcon, 'scenarioId': 'other'})


def test_interleaved_calls_are_reduced_in_terminal_sequence_order():
    events = [
        {'call_id': 'a', 'name': 'lookup', 'sequence': 3, 'status': 'success'},
        {'call_id': 'b', 'name': 'update', 'sequence': 2, 'status': 'success'},
        {'call_id': 'a', 'name': 'lookup', 'sequence': 1, 'status': 'requested'},
    ]
    assert [e['call_id'] for e in latest_tool_events(events)] == ['b', 'a']


def test_interleaved_workflow_scores_completion_order_on_direct_and_replay():
    payload = sample()
    first, second, *remaining = payload['action_trace']
    payload['action_trace'] = [
        {**first, 'call_id': 'a', 'sequence': 1, 'status': 'requested'},
        {**second, 'call_id': 'b', 'sequence': 2},
        {**first, 'call_id': 'a', 'sequence': 3},
        *[{**action, 'sequence': i} for i, action in enumerate(remaining, start=4)],
    ]
    original = run_scenario(payload, persist_artifacts=False)
    assert [e['call_id'] for e in original['action_trace'][:2]] == ['b', 'a']
    assert original['workflow_order_score'] == 0
    assert original['workflow_order_issues']
    assert original['verdict'] == 'needs_review'
    replay = run_scenario({'vcon': original['ietf_vcon_export']}, persist_artifacts=False)
    for key in ('workflow_order_score', 'workflow_order_issues', 'verdict', 'score_components'):
        assert original[key] == replay[key], key


@pytest.mark.parametrize('fields', [('action_trace',), ('final_state',), ('action_trace', 'final_state')])
def test_structured_only_run_exports_and_replays_portable_evidence(fields):
    payload = sample()
    payload.pop('transcript')
    for field in ('action_trace', 'final_state'):
        if field not in fields:
            payload.pop(field)
    original = run_scenario(payload, persist_artifacts=False)
    vcon = original['ietf_vcon_export']
    assert vcon is not None
    assert vcon['vcon'] == '0.4.0'
    assert vcon['dialog'] == []
    body = decode_evidence(vcon)
    assert len(body['tool_events']) == len(payload.get('action_trace', []))
    assert len(body['state_snapshots']) == int('final_state' in fields)
    assert 'dialog' not in vcon['attachments'][0]
    replay = run_scenario({'vcon': vcon}, persist_artifacts=False)
    for key in ('overall_score', 'verdict', 'task_completion_score', 'final_state_score',
                'completed_actions', 'missing_actions', 'score_components'):
        assert original.get(key) == replay.get(key), key


def test_saved_structured_only_run_download_uses_portable_profile():
    payload = sample()
    payload.pop('transcript')
    payload.update(user_id='structured-vcon-owner', project_id='structured-vcon-test')
    client = TestClient(app)
    response = client.post('/api/benchmarks/run', json=payload)
    assert response.status_code == 200
    original = response.json()
    url = f"/api/benchmarks/runs/{original['run_id']}/vcon"
    assert client.get(url, params={'user_id': 'other-owner'}).status_code == 404
    download = client.get(url, params={'user_id': payload['user_id']})
    assert download.status_code == 200
    vcon = download.json()['record']
    assert vcon['vcon'] == '0.4.0'
    assert vcon['dialog'] == []
    body = decode_evidence(vcon)
    assert len(body['tool_events']) == len(payload['action_trace'])
    assert body['state_snapshots'][0]['state'] == payload['final_state']
    replay = run_scenario({'vcon': vcon}, persist_artifacts=False)
    assert replay['score_components'] == original['score_components']
    assert replay['verdict'] == original['verdict']


def test_state_snapshots_preserve_before_after_and_require_unambiguous_final():
    payload = sample()
    body = evidence_body(state_snapshots=[
        {'snapshot_id': 'before', 'phase': 'before', 'source': 'crm', 'state': {'address': 'old'}},
        {'snapshot_id': 'after', 'phase': 'after', 'source': 'crm', 'state': {'address': 'new'}},
    ], final_state=payload['final_state'])
    vcon = attach_evidence(build_benchmark_vcon(payload, payload['transcript']), body)
    loaded, _ = intake_vcon({'vcon': vcon})
    assert loaded['final_state'] == payload['final_state']
    assert len(decode_evidence(vcon)['state_snapshots']) == 3
    with pytest.raises(ValueError, match='Conflicting captured'):
        evidence_body(state_snapshots=[{'phase': 'final', 'state': {'complete': False}}], final_state={'complete': True})


def test_optional_inline_recording_is_owner_checked_and_path_scoped(monkeypatch, tmp_path):
    import base64
    from app.services import execution_run_store

    payload = sample()
    exported = build_benchmark_vcon(payload, payload['transcript'])
    root = tmp_path / 'exec-test'
    root.mkdir()
    audio = root / 'audio.wav'
    audio.write_bytes(b'RIFF-test-audio')
    conversation = {'ietf_vcon_export': exported, 'recording': {'uri': str(audio), 'mime_type': 'audio/wav'}}
    monkeypatch.setattr(execution_run_store, 'RUNS_DIR', tmp_path)
    monkeypatch.setattr(execution_run_store, 'get_execution_run', lambda _: {'user_id': 'owner'})
    monkeypatch.setattr(execution_run_store, 'get_conversation', lambda *_: conversation)
    client = TestClient(app)
    url = '/api/execution/runs/exec-test/conversations/c/vcon'
    assert client.get(url, params={'user_id': 'other', 'include_audio': True}).status_code == 404
    response = client.get(url, params={'user_id': 'owner', 'include_audio': True})
    assert response.status_code == 200
    media = response.json()['dialog'][-1]
    assert base64.urlsafe_b64decode(media['body'] + '=' * (-len(media['body']) % 4)) == audio.read_bytes()
    assert str(tmp_path) not in response.text
    outside = tmp_path / 'private.wav'
    outside.write_bytes(b'private')
    conversation['recording']['uri'] = str(outside)
    assert client.get(url, params={'user_id': 'owner', 'include_audio': True}).status_code == 422
    assert client.get(url, params={'user_id': 'owner'}).json() == exported


def test_unknown_critical_extension_and_duplicate_sequence_rejected():
    payload = sample()
    vcon = build_benchmark_vcon(payload, payload['transcript'])
    vcon['critical'] = ['unrecognized']
    with pytest.raises(ValueError, match='critical'):
        intake_vcon({'vcon': vcon})
    vcon.pop('critical')
    events = vcon['attachments'][0]['body']['tool_events']
    events[1]['sequence'] = events[0]['sequence']
    with pytest.raises(ValueError, match='sequence'):
        intake_vcon({'vcon': vcon})


@pytest.mark.parametrize('statusless', [False, True])
def test_direct_lifecycle_and_statusless_trace_have_identical_replay(statusless):
    payload = sample()
    action = payload['action_trace'][0]
    if statusless:
        action.pop('status', None)
    else:
        action['call_id'] = 'call'
        action['sequence'] = 2
        payload['action_trace'].insert(0, {**action, 'status': 'requested', 'sequence': 1})
        for i, other in enumerate(payload['action_trace'][2:], start=3):
            other['sequence'] = i
    initial = run_scenario(payload, persist_artifacts=False)
    replay = run_scenario({'vcon': initial['ietf_vcon_export']}, persist_artifacts=False)
    for key in ('verdict', 'task_completion_score', 'final_state_score', 'missing_actions'):
        assert initial[key] == replay[key]
    assert action['action'] in initial['missing_actions'] if statusless else action['action'] not in initial['missing_actions']


def test_successful_distinct_retry_satisfies_action_without_erasing_failure():
    payload = sample()
    original = payload['action_trace'][0]
    original['call_id'] = 'retry'
    failed = {**original, 'call_id': 'first', 'status': 'timeout'}
    payload['action_trace'].insert(0, failed)
    report = run_scenario(payload, persist_artifacts=False)
    assert original['action'] not in report['missing_actions']
    assert len(decode_evidence(report['ietf_vcon_export'])['tool_events']) == len(payload['action_trace'])
    replay = run_scenario({'vcon': report['ietf_vcon_export']}, persist_artifacts=False)
    assert replay['verdict'] == report['verdict']


def test_profile_matches_published_json_schema():
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    repo = Path(__file__).resolve().parents[3]
    schema = json.loads((repo / 'docs/schemas/cae-execution-evidence-v1.json').read_text())
    Draft202012Validator.check_schema(schema)
    payload = sample()
    body = decode_evidence(build_benchmark_vcon(payload, payload['transcript']))
    Draft202012Validator(schema).validate(body)


@pytest.mark.parametrize('kind', ['benchmark', 'sample', 'telemetry'])
def test_new_portable_exports_have_validator_accepted_timestamps(kind):
    payload = sample()
    if kind == 'benchmark':
        vcon = run_scenario(payload, persist_artifacts=False)['ietf_vcon_export']
    elif kind == 'sample':
        response = TestClient(app).get('/api/benchmarks/evidence/sample-vcon', params={
            'suite_id': payload['suite_id'], 'scenario_id': payload['scenario_id'],
        })
        assert response.status_code == 200
        vcon = response.json()
    else:
        from app.services.voice_telemetry_vcon import build_telemetry_vcon
        vcon = build_telemetry_vcon(format='pipecat-function-events-v1', data={'events': []})
    assert vcon['updated_at'] == vcon['created_at']
    result = validate_ietf_vcon(vcon)
    assert not any('timestamp' in error for error in result['errors'])
    if kind == 'benchmark':
        assert result == {'valid': True, 'errors': []}


def test_profile_without_dialog_reevaluates_and_exports_empty_dialog():
    payload = sample()
    payload.pop('transcript')
    original = run_scenario(payload, persist_artifacts=False)
    vcon = deepcopy(original['ietf_vcon_export'])
    vcon.pop('dialog')
    unrelated = {'purpose': 'External metadata', 'body': {'retained': True}}
    vcon['attachments'].append(unrelated)
    intake_vcon({'vcon': vcon})
    replay = run_scenario({'vcon': vcon}, persist_artifacts=False)
    exported = replay['ietf_vcon_export']
    assert exported['dialog'] == []
    assert exported['analysis'][-1]['dialog'] == []
    assert exported['attachments'][-1] == unrelated
    assert exported['created_at'] == vcon['created_at']
    assert exported['updated_at'] == vcon['updated_at']
    assert 'dialog' not in vcon  # export must not mutate the input
    assert replay['score_components'] == original['score_components']
    response = TestClient(app).post('/api/benchmarks/run', json={'vcon': vcon})
    assert response.status_code == 200
    assert response.json()['ietf_vcon_export']['dialog'] == []


@pytest.mark.parametrize('field', ['created_at', 'updated_at'])
@pytest.mark.parametrize('invalid', ['missing', None, 'not-a-date', '2026-09-30T12:00:00', []])
@pytest.mark.parametrize('with_profile', [True, False])
def test_imported_vcon_invalid_timestamps_are_rejected_cleanly(field, invalid, with_profile):
    payload = sample()
    original = run_scenario(payload, persist_artifacts=False)['ietf_vcon_export']
    vcon = deepcopy(original)
    if not with_profile:
        vcon.pop('attachments')
    if invalid == 'missing':
        vcon.pop(field)
    else:
        vcon[field] = invalid
    request = {'suite_id': payload['suite_id'], 'scenario_id': payload['scenario_id'], 'vcon': vcon}
    if field == 'updated_at' and invalid == 'missing':
        intake_vcon(request)
        exported = build_benchmark_vcon(request, '')
        assert exported['created_at'] == original['created_at']
        assert validate_ietf_vcon(exported) == {'valid': True, 'errors': []}
        response = TestClient(app).post('/api/benchmarks/run', json=request)
        assert response.status_code == 200
        assert validate_ietf_vcon(response.json()['ietf_vcon_export']) == {'valid': True, 'errors': []}
        assert 'updated_at' not in vcon
        return
    with pytest.raises(ValueError, match=field):
        intake_vcon(request)
    with pytest.raises(ValueError, match=field):
        build_benchmark_vcon(request, '')
    client = TestClient(app)
    for url in ('/api/benchmarks/run', '/api/benchmarks/evidence/intake'):
        response = client.post(url, json=request)
        assert response.status_code == 422, response.text
        assert field in response.json()['detail']
    assert original[field]  # caller-owned source remains untouched


@pytest.mark.parametrize('analysis', [{}, None, 'invalid', 42])
def test_invalid_imported_analysis_returns_validation_error(analysis):
    vcon = run_scenario(sample(), persist_artifacts=False)['ietf_vcon_export']
    vcon['analysis'] = analysis
    with pytest.raises(ValueError, match='analysis must be an array'):
        intake_vcon({'vcon': vcon})
    client = TestClient(app)
    for url in ('/api/benchmarks/run', '/api/benchmarks/evidence/intake'):
        response = client.post(url, json={'vcon': vcon})
        assert response.status_code == 422
        assert 'analysis must be an array' in response.json()['detail']


def test_imported_profile_redacts_before_scoring_export_and_saved_download():
    import json
    vcon = run_scenario(sample(), persist_artifacts=False)['ietf_vcon_export']
    body = vcon['attachments'][0]['body']
    body['redactions'] = ['/previous/secret']
    body['tool_events'][0]['arguments'] = {'account_id': '123', 'api_key': 'credential-one'}
    body['tool_events'][0]['result'] = '{"found":true,"password":"credential-two"}'
    body['state_snapshots'][0]['state']['authorization'] = 'credential-three'
    original = deepcopy(vcon)
    client = TestClient(app)
    response = client.post('/api/benchmarks/run', json={'vcon': vcon, 'user_id': 'redaction-owner'})
    assert response.status_code == 200
    report = response.json()
    for secret in ('credential-one', 'credential-two', 'credential-three'):
        assert secret not in response.text
    cleaned = decode_evidence(report['ietf_vcon_export'])
    assert cleaned['tool_events'][0]['arguments'] == {'account_id': '123'}
    assert json.loads(cleaned['tool_events'][0]['result']) == {'found': True}
    assert set(cleaned['redactions']) == {
        '/previous/secret', '/tool_events/0/arguments/api_key',
        '/tool_events/0/result/password', '/state_snapshots/0/state/authorization',
    }
    download = client.get(f"/api/benchmarks/runs/{report['run_id']}/vcon", params={'user_id': 'redaction-owner'})
    assert download.status_code == 200
    assert decode_evidence(download.json()['record']) == cleaned
    saved = client.get(f"/api/benchmarks/runs/{report['run_id']}", params={'user_id': 'redaction-owner'})
    assert saved.status_code == 200
    assert all(secret not in saved.text for secret in ('credential-one', 'credential-two', 'credential-three'))
    replay = run_scenario({'vcon': report['ietf_vcon_export']}, persist_artifacts=False)
    assert replay['score_components'] == report['score_components']
    assert decode_evidence(replay['ietf_vcon_export'])['redactions'] == cleaned['redactions']
    assert vcon == original


@pytest.mark.parametrize('parties', [None, {}, 'invalid', [], [None], ['caller'], [42]])
def test_malformed_imported_parties_return_validation_error(parties):
    vcon = run_scenario(sample(), persist_artifacts=False)['ietf_vcon_export']
    vcon['parties'] = parties
    for with_profile in (True, False):
        if not with_profile:
            vcon.pop('attachments')
        with pytest.raises(ValueError, match='parties'):
            intake_vcon({'vcon': vcon})
        with pytest.raises(ValueError, match='parties'):
            attach_evidence(vcon, evidence_body())
        response = TestClient(app).post('/api/benchmarks/run', json={'vcon': vcon})
        assert response.status_code == 422
        assert 'parties' in response.json()['detail']


@pytest.mark.parametrize('field,invalid', [
    ('dialog', None), ('dialog', {}), ('dialog', 'invalid'), ('dialog', 42),
    ('dialog', [None]), ('dialog', ['invalid']),
    ('uuid', 'missing'), ('uuid', None), ('uuid', ''), ('uuid', 'not-a-uuid'),
    ('uuid', []), ('uuid', {}), ('uuid', 42),
])
@pytest.mark.parametrize('with_profile', [True, False])
def test_invalid_portable_uuid_and_dialog_are_rejected_before_scoring(field, invalid, with_profile):
    payload = sample()
    vcon = run_scenario(payload, persist_artifacts=False)['ietf_vcon_export']
    if not with_profile:
        vcon.pop('attachments')
    if invalid == 'missing':
        vcon.pop(field)
    else:
        vcon[field] = invalid
    original = deepcopy(vcon)
    request = {'suite_id': payload['suite_id'], 'scenario_id': payload['scenario_id'],
               'transcript': payload['transcript'], 'vcon': vcon}
    with pytest.raises(ValueError, match=field):
        intake_vcon(request)
    with pytest.raises(ValueError, match=field):
        build_benchmark_vcon(request, '')
    client = TestClient(app)
    for url in ('/api/benchmarks/run', '/api/benchmarks/evidence/intake'):
        response = client.post(url, json=request)
        assert response.status_code == 422
        assert field in response.json()['detail']
    assert vcon == original
