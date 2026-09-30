from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.benchmark_service import get_suite, run_scenario
from app.services.execution_vcon import build_ietf_execution_vcon
from app.services.vcon_evidence import (
    PURPOSE, attach_evidence, build_benchmark_vcon, decode_evidence, evidence_body, intake_vcon,
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
