from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services import acc_connection, agent_store, execution_run_store
from app.services.execution_runner import (
    _scenario_user_opener,
    execute_execution_run,
    start_execution_run,
)
from app.services.target_secrets import resolve_http_target_secret
from app.schemas.execution import ExecutionRunCreateRequest


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_agent_registry(tmp_path, monkeypatch):
    """Keep agent CRUD tests from reading or writing the local demo registry."""
    monkeypatch.setattr(agent_store, 'AGENTS_DIR', tmp_path / 'agents')
    execution_run_store.reset_execution_runs_for_tests()
    acc_connection.reset_acc_connections_for_tests()
    agent_store.reset_agents_for_tests(clear_files=True)
    yield
    execution_run_store.reset_execution_runs_for_tests()
    acc_connection.reset_acc_connections_for_tests()
    agent_store.reset_agents_for_tests(clear_files=True)


def test_agent_crud_round_trip():
    listed = client.get('/api/agents')
    assert listed.status_code == 200
    agents = listed.json()['agents']
    assert {item['id'] for item in agents} >= {
        'mock-text-agent',
        'acc-voice-fixture-agent',
        'generalist-voice-agent',
        'pipecat-public-demo',
        'holyguacamole-signalwire-agent',
    }

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


def test_http_target_requires_real_connection_configuration():
    missing = client.post(
        '/api/agents',
        json={'name': 'No endpoint', 'channel': 'text', 'target': 'http_endpoint'},
    )
    assert missing.status_code == 422
    assert 'endpoint_url' in missing.text

    inline_credentials = client.post(
        '/api/agents',
        json={
            'name': 'Unsafe endpoint',
            'channel': 'text',
            'target': 'http_endpoint',
            'connection': {'endpoint_url': 'https://user:secret@example.test/chat'},
        },
    )
    assert inline_credentials.status_code == 422
    assert 'Do not embed credentials' in inline_credentials.text

    arbitrary_environment_variable = client.post(
        '/api/agents',
        json={
            'name': 'Unsafe credential reference',
            'channel': 'text',
            'target': 'http_endpoint',
            'connection': {
                'endpoint_url': 'https://example.test/chat',
                'auth_type': 'bearer_secret',
                'secret_ref': 'AWS_SECRET_ACCESS_KEY',
            },
        },
    )
    assert arbitrary_environment_variable.status_code == 422
    assert 'string_pattern_mismatch' in arbitrary_environment_variable.text


def test_agent_options_expose_adapter_tester_executor_defaults():
    response = client.get('/api/agents/options')
    assert response.status_code == 200
    targets = {item['id']: item for item in response.json()['targets']}

    http_target = targets['http_endpoint']
    assert http_target['channel'] == 'text'
    assert http_target['group'] == 'live_connection'
    assert http_target['available'] is True
    assert http_target['requires_connection'] == ['endpoint_url']
    assert http_target['defaults'] == {
        'mode': 'text_callable',
        'tester_id': 'scenario_simulator',
        'executor_id': 'local_async_runner',
        'audio_transport': 'none',
    }

    sip_target = targets['sip_agent']
    assert sip_target['channel'] == 'voice'
    assert sip_target['available'] is False
    assert sip_target['requires_connection'] == ['acc_base_url', 'sip_uri']
    assert sip_target['defaults']['executor_id'] == 'acc_sip'
    assert 'cannot execute this adapter' in sip_target['unavailable_reason']

    builtin_voice = targets['builtin_sample_voice']
    assert builtin_voice['group'] == 'built_in_sample'
    assert builtin_voice['defaults']['tester_id'] == 'pipecat_tester'
    assert builtin_voice['defaults']['executor_id'] == 'cae_local_audio_loop'

    public_pipecat = targets['pipecat_public_demo']
    assert public_pipecat['label'] == 'Pipecat demo'
    assert public_pipecat['channel'] == 'voice'
    assert public_pipecat['available'] is True
    assert public_pipecat['requires_connection'] == ['endpoint_url']
    assert public_pipecat['default_connection'] == {'endpoint_url': 'https://www.pipecat.ai/'}
    assert public_pipecat['defaults'] == {
        'mode': 'pipecat_webrtc',
        'tester_id': 'pipecat_tester',
        'executor_id': 'pipecat_public_daily',
        'audio_transport': 'pipecat_daily_webrtc',
    }

    signalwire = targets['signalwire_holy_guacamole']
    assert signalwire['label'] == 'Holy Guacamole SignalWire'
    assert signalwire['channel'] == 'voice'
    assert signalwire['available'] is True
    assert signalwire['requires_connection'] == ['endpoint_url']
    assert signalwire['default_connection'] == {'endpoint_url': 'https://holyguacamole.signalwire.me/'}
    assert signalwire['defaults'] == {
        'mode': 'pipecat_webrtc',
        'tester_id': 'pipecat_tester',
        'executor_id': 'signalwire_public_direct',
        'audio_transport': 'signalwire_direct_webrtc',
        'max_exchanges': 1,
        'max_exchanges_configurable': False,
    }


def test_pipecat_public_target_accepts_fixed_public_url():
    created = client.post(
        '/api/agents',
        json={
            'name': 'Public Pipecat target',
            'channel': 'voice',
            'target': 'pipecat_public_demo',
            'environment': 'production',
            'connection': {'endpoint_url': 'https://www.pipecat.ai/'},
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()['connection']['endpoint_url'] == 'https://www.pipecat.ai/'


def test_signalwire_holyguacamole_target_accepts_fixed_public_url():
    created = client.post(
        '/api/agents',
        json={
            'name': 'Holy Guacamole public target',
            'channel': 'voice',
            'target': 'signalwire_holy_guacamole',
            'environment': 'production',
            'connection': {'endpoint_url': 'https://holyguacamole.signalwire.me/'},
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()['connection']['endpoint_url'] == 'https://holyguacamole.signalwire.me/'


@pytest.mark.parametrize('endpoint_url', [
    'https://internal.example.test/',
    'https://www.pipecat.ai/other-path',
    'https://www.pipecat.ai:444/',
    'https://user:secret@www.pipecat.ai/',
    'https://www.pipecat.ai/?token=secret',
])
def test_pipecat_public_target_rejects_non_public_url(endpoint_url):
    response = client.post(
        '/api/agents',
        json={
            'name': 'Unsafe public target',
            'channel': 'voice',
            'target': 'pipecat_public_demo',
            'connection': {'endpoint_url': endpoint_url},
        },
    )
    assert response.status_code == 422
    assert 'https://www.pipecat.ai/' in response.text


@pytest.mark.parametrize('endpoint_url', [
    'https://internal.example.test/',
    'https://holyguacamole.signalwire.me/menu',
    'https://holyguacamole.signalwire.me:444/',
    'https://user:secret@holyguacamole.signalwire.me/',
    'https://holyguacamole.signalwire.me/?token=secret',
])
def test_signalwire_holyguacamole_target_rejects_non_public_url(endpoint_url):
    response = client.post(
        '/api/agents',
        json={
            'name': 'Unsafe Holy Guacamole target',
            'channel': 'voice',
            'target': 'signalwire_holy_guacamole',
            'connection': {'endpoint_url': endpoint_url},
        },
    )
    assert response.status_code == 422
    assert 'https://holyguacamole.signalwire.me/' in response.text


def test_http_target_credentials_only_resolve_from_dedicated_namespace(monkeypatch):
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'must-not-be-read')

    with pytest.raises(ValueError, match='credential is not configured'):
        resolve_http_target_secret('aws-secret-access-key')

    monkeypatch.setenv('CAE_HTTP_TARGET_SECRET_AWS_SECRET_ACCESS_KEY', 'approved-target-secret')
    assert resolve_http_target_secret('aws-secret-access-key') == 'approved-target-secret'


def test_http_target_executes_black_box_contract_and_persists_tester_provenance(monkeypatch):
    from app.services import execution_runner

    requests = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({'result': {'reply': 'Please confirm your ZIP code and phone number.'}}).encode()

    def fake_urlopen(request, *, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv('SUPPORT_AGENT_TOKEN', 'must-not-be-read')
    monkeypatch.setenv('CAE_HTTP_TARGET_SECRET_SUPPORT_AGENT_TOKEN', 'not-persisted')
    monkeypatch.setattr(execution_runner, 'urlopen', fake_urlopen)
    created = client.post(
        '/api/agents',
        json={
            'name': 'Staging HTTP support',
            'channel': 'text',
            'target': 'http_endpoint',
            'environment': 'staging',
            'connection': {
                'endpoint_url': 'https://support.example.test/chat',
                'auth_type': 'bearer_secret',
                'secret_ref': 'support-agent-token',
                'response_path': 'result.reply',
                'timeout_ms': 5000,
            },
        },
    )
    assert created.status_code == 200, created.text
    assert 'not-persisted' not in created.text

    payload = ExecutionRunCreateRequest(
        suite_id='call-center-voice-ai',
        scenario_ids=['billing-address-change'],
        agent_id=created.json()['id'],
        tester_id='scenario_simulator',
        executor_id='local_async_runner',
        user_id='agent-runs-user',
        project_id='agent-runs-project',
        evaluate=False,
    )
    queued = start_execution_run(payload)
    assert queued['tester_id'] == 'scenario_simulator'
    assert queued['executor_id'] == 'local_async_runner'
    assert queued['execution_snapshot']['agent']['target'] == 'http_endpoint'
    finished = execute_execution_run(queued['execution_run_id'], payload)

    assert finished['status'] == 'completed'
    conversation = finished['conversations'][0]
    assert conversation['turns'][1]['text'] == 'Please confirm your ZIP code and phone number.'
    provenance = conversation['final_state']['runtime_provenance']
    assert provenance['fixture_backed'] is False
    assert provenance['trace_visibility'] == 'black_box'
    assert provenance['tester_id'] == 'scenario_simulator'
    request, timeout = requests[0]
    assert timeout == 5
    assert request.headers['Authorization'] == 'Bearer not-persisted'
    posted = json.loads(request.data)
    assert posted['scenario']['id'] == 'billing-address-change'
    assert posted['history'][0]['role'] == 'user'
    assert posted['message'].startswith('Hi,')
    assert 'A busy customer who' not in posted['message']


def test_execution_rejects_tester_incompatible_with_mode():
    with pytest.raises(ValueError, match='text_callable mode requires tester_id=scenario_simulator'):
        ExecutionRunCreateRequest(mode='text_callable', tester_id='fixture_replay')
    with pytest.raises(ValueError, match='voice_fixture mode requires tester_id=fixture_replay'):
        ExecutionRunCreateRequest(mode='voice_fixture', tester_id='scenario_simulator')


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
    assert queued['provenance']['honesty_label'] == (
        'Offline synthetic sample · generated transcript and scoring · no real agent or provider interaction'
    )

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
    assert 'missing_actions' in conversation['evaluation_findings']
    assert 'rubric_checks' in conversation['evaluation_findings']
    assert conversation['evaluation_findings']['scoring_mode'] == 'agentic'
    assert conversation['evaluation_findings']['score_components']
    assert conversation['evaluation_findings']['scenario_contract']['required_actions']
    assert isinstance(conversation.get('timeline'), list)
    assert finished.get('run_snapshot_path')

    execution_run_store.reset_execution_runs_for_tests()
    reloaded = execution_run_store.get_execution_run(queued['execution_run_id'])
    assert reloaded is not None
    assert reloaded['execution_run_id'] == queued['execution_run_id']
    assert reloaded['conversations'][0]['metrics_summary']['turn_count'] >= 1


def test_builtin_sample_voice_agent_run_uses_local_audio_loop_provenance():
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id='generalist-voice-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )
    assert queued['mode'] == 'pipecat_webrtc'
    provenance = queued['provenance']
    assert provenance['target_kind'] == 'builtin_sample_voice'
    assert provenance['tester_id'] == 'pipecat_tester'
    assert provenance['executor_id'] == 'cae_local_audio_loop'
    assert provenance['evidence_source'] == 'local_audio_loop'
    assert provenance['live_external_connection'] is False
    assert provenance['saved_evidence'] is False
    assert provenance['synthetic_media'] is True
    assert provenance['honesty_label'] == (
        'Built-in generalist agent · current-run local audio and scoring · no browser, phone, or SIP call'
    )
    finished = execute_execution_run(
        queued['execution_run_id'],
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id='generalist-voice-agent',
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


def test_public_pipecat_agent_uses_direct_daily_executor(monkeypatch, tmp_path):
    from app.services import execution_runner
    from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn

    response_audio = tmp_path / 'public-target.wav'
    response_audio.write_bytes(b'current-run-target-audio')

    def fake_public_call(**kwargs):
        assert kwargs['caller_text']
        assert kwargs['timeout_seconds'] == 60
        assert kwargs['max_exchanges'] == 3
        assert kwargs['execution_run_id'] == queued['execution_run_id']
        assert kwargs['scenario']['id'] == 'cancellation-rescue'
        return {
            'target': {'selected_agent': '10-gradium'},
            'connection': {'connected': True, 'response_complete': True},
            'latency_metrics': {
                'tester_speech_end_to_first_target_audio_received_ms': 240.5,
                'tester_speech_end_to_first_target_speech_received_ms': 240.5,
                'total_run_ms': 1200.0,
            },
            'exchanges': [{
                'turn_pair': 1,
                'latency': {
                    'tester_speech_end_to_first_target_audio_received_ms': 240.5,
                    'tester_speech_end_to_first_target_speech_received_ms': 240.5,
                    'first_target_media_frame_latency_ms': 0.2,
                    'signal_boundary': 'silero_vad_speech_onset',
                    'response_complete_latency_ms': 800.0,
                },
            }],
            'transcription_turns': [
                TranscriptionTurn(
                    turn_index=1,
                    speaker='Caller',
                    text='I need help with a cancellation.',
                    source='pipecat_public_daily',
                    direction='tester_to_target',
                    evidence_role='tester',
                ),
                TranscriptionTurn(
                    turn_index=2,
                    speaker='Agent',
                    text='I can help with that.',
                    source='pipecat_public_daily',
                    direction='target_to_tester',
                    evidence_role='target',
                ),
            ],
            'recording_handle': AudioRecordingHandle(
                uri=str(response_audio),
                bytes_captured=response_audio.stat().st_size,
                transport='pipecat_daily_webrtc',
            ),
        }

    observed_benchmark_request = None

    def fake_run_scenario(request):
        nonlocal observed_benchmark_request
        observed_benchmark_request = request
        return {'verdict': 'pass', 'overall_score': 100}

    monkeypatch.setattr(execution_runner, 'run_public_pipecat_call', fake_public_call)
    monkeypatch.setattr(execution_runner, 'run_scenario', fake_run_scenario)
    payload = ExecutionRunCreateRequest(
        suite_id='call-center-voice-ai',
        scenario_ids=['cancellation-rescue'],
        agent_id='pipecat-public-demo',
        model_name='gpt-5.4-mini',
        duplex_timeout_seconds=60,
        evaluate=True,
        user_id='agent-runs-user',
        project_id='agent-runs-project',
    )
    queued = start_execution_run(payload)

    assert queued['mode'] == 'pipecat_webrtc'
    assert queued['executor_id'] == 'pipecat_public_daily'
    assert queued['max_exchanges'] == 3
    assert queued['execution_snapshot']['request']['audio_transport'] == 'pipecat_daily_webrtc'
    assert queued['model_name'] == '10-gradium'
    assert queued['execution_snapshot']['request']['model_name'] == '10-gradium'
    assert queued['provenance']['live_external_connection'] is True
    assert queued['provenance']['evidence_source'] == 'external_webrtc'

    finished = execute_execution_run(queued['execution_run_id'], payload)
    conversation = finished['conversations'][0]
    assert conversation['status'] == 'completed'
    assert conversation['audio_session']['transport'] == 'pipecat_daily_webrtc'
    assert conversation['audio_session']['negotiated'] is True
    assert conversation['recording']['transport'] == 'pipecat_daily_webrtc'
    assert conversation['recording']['recording_url'].endswith(
        f'/recording?user_id={payload.user_id}'
    )
    assert conversation['latency_marks'][0]['kind'] == (
        'tester_speech_end_to_first_target_speech_received'
    )
    assert conversation['latency_marks'][0]['latency_ms'] == 240.5
    assert conversation['latency_marks'][0]['first_target_media_frame_latency_ms'] == 0.2
    assert conversation['latency_marks'][0]['signal_boundary'] == 'silero_vad_speech_onset'
    assert conversation['latency_marks'][0]['response_complete_latency_ms'] == 800.0
    assert conversation['latency_marks'][0]['measurement_scope'] == 'remote_target_observed_at_tester'
    assert conversation['latency_marks'][0]['remote_target'] is True
    assert conversation['final_state']['runtime_provenance']['browser_peer'] is False
    assert [turn['speaker'] for turn in conversation['turns']] == ['caller', 'agent']
    assert observed_benchmark_request is not None
    assert 'final_state' not in observed_benchmark_request.model_fields_set
    assert observed_benchmark_request.final_state == {}


def test_signalwire_holyguacamole_agent_uses_gated_direct_executor(monkeypatch):
    from app.services import execution_runner
    from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn

    def fake_signalwire_call(**kwargs):
        assert kwargs['caller_text']
        assert kwargs['timeout_seconds'] == 60
        assert kwargs['execution_run_id'] == queued['execution_run_id']
        assert kwargs['scenario']['id'] == 'cancellation-rescue'
        assert kwargs['event_observer'] is not None
        kwargs['event_observer']({
            'speaker': 'Agent',
            'text': '',
            'direction': 'target_to_tester',
            'update_live_audio_key': '1:target_to_tester',
            'live_audio_key': '1:target_to_tester',
            'audio': b'current-run-live-target-wav',
            'frame_metadata': {
                'transport': 'signalwire_direct_webrtc',
                'current_run': True,
                'listener_media_key': 'conversation-1:1:target_to_tester',
            },
        })
        response_audio = kwargs['artifact_dir'] / 'signalwire-direct' / 'fake' / 'target-audio.wav'
        response_audio.parent.mkdir(parents=True, exist_ok=True)
        response_audio.write_bytes(b'current-run-signalwire-audio')
        return {
            'connection': {'call_connected': True, 'remote_audio_track_seen': True},
            'latency_metrics': {
                'tester_speech_end_to_first_target_audio_received_ms': 640.0,
                'total_run_ms': 6000.0,
            },
            'media': {'target_audio_duration_ms': 5000},
            'app_messages': [{'type': 'call_status', 'status': 'connected'}],
            'transcription_turns': [
                TranscriptionTurn(
                    turn_index=1,
                    speaker='Caller',
                    text='I need help with a cancellation.',
                    source='signalwire_direct_webrtc',
                    direction='tester_to_target',
                    evidence_role='tester',
                ),
            ],
            'recording_handle': AudioRecordingHandle(
                uri=str(response_audio),
                mime_type='audio/wav',
                bytes_captured=response_audio.stat().st_size,
                transport='signalwire_direct_webrtc',
            ),
        }

    monkeypatch.setattr(execution_runner, 'run_signalwire_holyguacamole_call', fake_signalwire_call)
    payload = ExecutionRunCreateRequest(
        suite_id='call-center-voice-ai',
        scenario_ids=['cancellation-rescue'],
        agent_id='holyguacamole-signalwire-agent',
        duplex_timeout_seconds=60,
        evaluate=False,
        user_id='agent-runs-user',
        project_id='agent-runs-project',
    )
    queued = start_execution_run(payload)

    assert queued['mode'] == 'pipecat_webrtc'
    assert queued['executor_id'] == 'signalwire_public_direct'
    assert queued['max_exchanges'] == 1
    assert queued['execution_snapshot']['request']['audio_transport'] == 'signalwire_direct_webrtc'
    assert queued['execution_snapshot']['request']['max_exchanges'] == 1
    assert queued['provenance']['target_kind'] == 'signalwire_holy_guacamole'
    assert queued['provenance']['live_external_connection'] is True
    assert queued['provenance']['evidence_source'] == 'external_webrtc'

    finished = execute_execution_run(queued['execution_run_id'], payload)
    conversation = finished['conversations'][0]
    assert conversation['status'] == 'completed'
    assert conversation['audio_session']['transport'] == 'signalwire_direct_webrtc'
    assert conversation['audio_session']['provider'] == 'signalwire'
    assert conversation['recording']['transport'] == 'signalwire_direct_webrtc'
    assert conversation['recording']['mime_type'] == 'audio/wav'
    assert conversation['live_events'][0]['kind'] == 'audio'
    assert conversation['live_events'][0]['speaker'] == 'Agent'
    assert conversation['live_events'][0]['media_url'].endswith(
        f'/audio/1?user_id={payload.user_id}'
    )
    assert conversation['live_events'][0]['frame_metadata']['listener_media_key'] == (
        'conversation-1:1:target_to_tester'
    )
    recording_response = client.get(conversation['recording']['recording_url'])
    assert recording_response.status_code == 200
    assert recording_response.headers['content-type'] == 'audio/wav'
    assert recording_response.content == b'current-run-signalwire-audio'
    recording_attachment = conversation['vcon_export']['attachments'][0]
    assert recording_attachment['type'] == 'recording'
    assert recording_attachment['url'] == conversation['recording']['recording_url']
    assert conversation['recording']['uri'] not in json.dumps(conversation['vcon_export'])
    assert conversation['latency_marks'][0]['kind'] == 'tester_speech_end_to_first_target_audio'
    assert conversation['latency_marks'][0]['latency_ms'] == 640.0
    assert conversation['final_state']['runtime_provenance']['browser_peer'] is False
    assert conversation['final_state']['runtime_provenance']['execution_engine'] == 'signalwire_js_node_webrtc'
    assert conversation['final_state']['runtime_provenance']['guest_token_persisted'] is False
    assert conversation['final_state']['runtime_provenance']['target_speech_transcript'] == (
        'untranscribed_remote_audio'
    )
    assert [turn['speaker'] for turn in conversation['turns']] == ['caller']


def test_signalwire_holyguacamole_evaluation_without_agent_transcript_needs_review(monkeypatch):
    from app.services import execution_runner
    from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn

    def fake_signalwire_call(**kwargs):
        response_audio = kwargs['artifact_dir'] / 'signalwire-direct' / 'fake' / 'target-audio.wav'
        response_audio.parent.mkdir(parents=True, exist_ok=True)
        response_audio.write_bytes(b'current-run-signalwire-audio')
        return {
            'connection': {
                'call_connected': True,
                'remote_audio_track_seen': True,
                'remote_audio_sample_seen': True,
            },
            'latency_metrics': {'tester_speech_end_to_first_target_audio_received_ms': 640.0},
            'media': {'target_audio_duration_ms': 5000},
            'app_messages': [{'type': 'call_status', 'status': 'connected'}],
            'transcription_turns': [
                TranscriptionTurn(
                    turn_index=1,
                    speaker='Caller',
                    text='I need help with a cancellation.',
                    source='signalwire_direct_webrtc',
                    direction='tester_to_target',
                    evidence_role='tester',
                ),
            ],
            'recording_handle': AudioRecordingHandle(
                uri=str(response_audio),
                mime_type='audio/wav',
                bytes_captured=response_audio.stat().st_size,
                transport='signalwire_direct_webrtc',
            ),
        }

    monkeypatch.setattr(execution_runner, 'run_signalwire_holyguacamole_call', fake_signalwire_call)
    payload = ExecutionRunCreateRequest(
        suite_id='call-center-voice-ai',
        scenario_ids=['cancellation-rescue'],
        agent_id='holyguacamole-signalwire-agent',
        duplex_timeout_seconds=60,
        evaluate=True,
        user_id='agent-runs-user',
        project_id='agent-runs-project',
    )

    queued = start_execution_run(payload)
    finished = execute_execution_run(queued['execution_run_id'], payload)
    conversation = finished['conversations'][0]

    assert finished['status'] == 'needs_review'
    assert conversation['status'] == 'needs_review'
    assert conversation['verdict'] == 'needs_review'
    assert conversation['score'] is None
    assert 'overall_score' not in conversation['evaluation_findings']
    assert conversation['evaluation_findings']['failure_categories'] == [
        'missing_grounded_agent_transcript'
    ]
    assert conversation['final_state']['runtime_provenance']['target_speech_transcript'] == (
        'untranscribed_remote_audio'
    )


def test_signalwire_holyguacamole_rejects_multi_exchange_request():
    payload = ExecutionRunCreateRequest(
        suite_id='call-center-voice-ai',
        scenario_ids=['cancellation-rescue'],
        agent_id='holyguacamole-signalwire-agent',
        max_exchanges=2,
        user_id='agent-runs-user',
        project_id='agent-runs-project',
    )

    with pytest.raises(ValueError, match='max_exchanges=1'):
        start_execution_run(payload)


def test_user_created_scenario_opener_preserves_custom_prompt_details():
    scenario = {
        'id': 'account-access-issue',
        'title': 'Account access issue',
        'source': 'user_created',
        'simulated_user_prompt': (
            'The user is unable to access his account. He recently changed his password, '
            'but when he tries to log in, the system says that the password is incorrect.'
        ),
    }

    opener = _scenario_user_opener(scenario)

    assert 'recently changed his password' in opener
    assert 'password is incorrect' in opener


def test_saved_voice_agent_ignores_serialized_request_placeholders():
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id='generalist-voice-agent',
            mode='text_callable',
            tester_id='scenario_simulator',
            executor_id='local_async_runner',
            audio_transport='none',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )

    assert queued['mode'] == 'pipecat_webrtc'
    assert queued['tester_id'] == 'pipecat_tester'
    assert queued['executor_id'] == 'cae_local_audio_loop'
    assert queued['execution_snapshot']['request']['audio_transport'] == 'pipecat_small_webrtc'


def test_acc_readiness_does_not_overclaim_cae_executor_availability(monkeypatch):
    monkeypatch.setenv('ACC_BASE_URL', 'http://acc.local:8026')
    blocked = client.post(
        '/api/agents',
        json={
            'name': 'SIP bot',
            'channel': 'voice',
            'target': 'sip_agent',
            'connection': {
                'sip_uri': 'sip:agent@example.com',
                'acc_base_url': 'http://acc.local:8026',
            },
        },
    )
    assert blocked.status_code in {400, 422}
    detail = blocked.json().get('detail')
    detail_text = detail if isinstance(detail, str) else str(detail)
    assert 'ACC' in detail_text

    status = client.get('/api/execution/acc-connection')
    assert status.status_code == 200
    assert status.json()['connected'] is False
    assert status.json()['label'] == 'Requires ACC connection'

    readiness_payload = {
        'ok': True,
        'route': '/api/pipecat-media-engine/readiness',
        'sharedEngineContract': {
            'requiredAdapters': [
                {'id': 'browser_webrtc', 'implementedNow': True},
                {
                    'id': 'sip_freeswitch_verto',
                    'implementedNow': True,
                    'liveMediaProofComplete': True,
                },
                {
                    'id': 'signalwire_sip_trunk',
                    'implementedNow': False,
                    'blocker': 'PSTN trunk routing is not ready.',
                },
            ],
        },
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return readiness_payload

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            assert url == 'http://acc.local:8026/api/pipecat-media-engine/readiness'
            return FakeResponse()

    monkeypatch.setattr(acc_connection.httpx, 'Client', FakeClient)
    tested = client.post(
        '/api/execution/acc-connection/test',
        json={'base_url': 'http://acc.local:8026'},
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()['connected'] is True
    assert tested.json()['destinations']['sip_agent']['acc_ready'] is True
    assert tested.json()['destinations']['sip_agent']['cae_executor_available'] is False
    assert tested.json()['destinations']['sip_agent']['creatable'] is False
    assert tested.json()['destinations']['browser_webrtc_agent']['creatable'] is False
    assert tested.json()['destinations']['phone_agent']['creatable'] is False

    still_blocked = client.post(
        '/api/agents',
        json={
            'name': 'SIP bot',
            'channel': 'voice',
            'target': 'sip_agent',
            'connection': {
                'sip_uri': 'sip:agent@example.com:5060;transport=tcp',
                'acc_base_url': 'http://acc.local:8026',
            },
        },
    )
    assert still_blocked.status_code in {400, 422}
    assert 'execution adapter is not implemented' in still_blocked.text


def test_acc_readiness_rejects_unconfigured_probe_destinations(monkeypatch):
    monkeypatch.setenv('ACC_BASE_URL', 'http://127.0.0.1:8026')

    class RejectNetworkClient:
        def __init__(self, **_kwargs):
            raise AssertionError('Untrusted ACC destinations must be rejected before network access.')

    monkeypatch.setattr(acc_connection.httpx, 'Client', RejectNetworkClient)

    for base_url in (
        'http://169.254.169.254',
        'http://127.0.0.1:22',
        'http://10.0.0.5:8080',
        'https://example.test',
    ):
        response = client.post('/api/execution/acc-connection/test', json={'base_url': base_url})
        assert response.status_code == 400
        assert 'operator-configured ACC_BASE_URL' in response.text


def test_saved_voice_replay_is_not_a_creatable_target():
    response = client.post(
        '/api/agents',
        json={
            'name': 'Saved evidence is not a target',
            'channel': 'voice',
            'target': 'voice_fixture',
        },
    )
    assert response.status_code == 422
    assert 'evidence, not an agent target' in response.text


def test_rejects_incompatible_executor_for_builtin_sample_voice():
    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'agent_id': 'generalist-voice-agent',
            'executor_id': 'acc_sip',
            'mode': 'pipecat_webrtc',
            'user_id': 'agent-runs-user',
            'project_id': 'agent-runs-project',
            'iterations': 1,
        },
    )
    assert response.status_code == 422
    assert 'cae_local_audio_loop' in response.text


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
    assert queued_default['model_name'] == 'gpt-5.4-mini'

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

    signalwire = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'agent_id': 'holyguacamole-signalwire-agent',
            'user_id': 'agent-runs-user',
            'project_id': 'agent-runs-project',
            'model_name': 'gpt-4.1',
            'iterations': 1,
        },
    )
    assert signalwire.status_code == 200, signalwire.text
    signalwire_body = signalwire.json()
    assert signalwire_body['model_name'] == 'signalwire-ai-agent'
    assert signalwire_body['execution_snapshot']['request']['model_name'] == (
        'signalwire-ai-agent'
    )


def test_generalist_voice_honors_reference_model_env_and_explicit_override(monkeypatch):
    monkeypatch.setenv('REFERENCE_LLM_MODEL', 'compatible-local-model')
    base = {
        'suite_id': 'call-center-voice-ai',
        'scenario_ids': ['cancellation-rescue'],
        'agent_id': 'generalist-voice-agent',
        'user_id': 'agent-runs-user',
        'project_id': 'agent-runs-project',
        'iterations': 1,
    }

    from_env = start_execution_run(ExecutionRunCreateRequest(**base))
    assert from_env['model_name'] == 'compatible-local-model'
    assert from_env['execution_snapshot']['request']['model_name'] == 'compatible-local-model'

    explicit = start_execution_run(
        ExecutionRunCreateRequest(**base, model_name='per-run-model')
    )
    assert explicit['model_name'] == 'per-run-model'
    assert explicit['execution_snapshot']['request']['model_name'] == 'per-run-model'


def test_rejects_unsafe_agent_id_and_seed_mutations():
    bad = client.post(
        '/api/agents',
        json={'id': '../evil', 'name': 'Evil', 'channel': 'text', 'target': 'mock_agent'},
    )
    assert bad.status_code == 400

    seed_delete = client.delete('/api/agents/mock-text-agent')
    assert seed_delete.status_code == 400
    assert 'Seed agent' in seed_delete.json()['detail']

    seed_edit = client.patch('/api/agents/mock-text-agent', json={'target': 'offline_acc_fixture'})
    assert seed_edit.status_code == 422
    assert 'evidence, not an agent target' in seed_edit.text
    assert client.get('/api/agents/mock-text-agent').json()['target'] == 'mock_agent'


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


@pytest.mark.parametrize(
    ('channel', 'target'),
    [
        ('text', 'voice_fixture'),
        ('voice', 'mock_agent'),
        ('voice', 'openai_codex'),
    ],
)
def test_rejects_incompatible_agent_channel_target_pairs(channel: str, target: str):
    created = client.post(
        '/api/agents',
        json={'name': 'Incompatible agent', 'channel': channel, 'target': target},
    )
    assert created.status_code == 422
    assert 'requires one of' in created.text

    valid = client.post(
        '/api/agents',
        json={'name': 'Text agent', 'channel': 'text', 'target': 'mock_agent'},
    )
    assert valid.status_code == 200

    patched = client.patch(
        f"/api/agents/{valid.json()['id']}",
        json={'target': 'voice_fixture'},
    )
    assert patched.status_code == 422
    assert 'evidence, not an agent target' in patched.text


def test_saved_text_replay_is_not_a_creatable_target():
    response = client.post(
        '/api/agents',
        json={
            'name': 'Saved text evidence is not a target',
            'channel': 'text',
            'target': 'offline_acc_fixture',
        },
    )
    assert response.status_code == 422
    assert 'evidence, not an agent target' in response.text


def test_agent_payload_rejects_execution_mode_that_bypasses_selected_text_target():
    from app.services.execution_runner import _resolve_agent_payload

    with pytest.raises(ValueError, match='not compatible with target'):
        _resolve_agent_payload(
            ExecutionRunCreateRequest(
                suite_id='call-center-voice-ai',
                scenario_ids=['cancellation-rescue'],
                agent_id='mock-text-agent',
                mode='pipecat_webrtc',
                user_id='agent-runs-user',
                project_id='agent-runs-project',
                iterations=1,
            )
        )


def test_voice_fixture_target_allows_pipecat_capture_proof_mode():
    from app.services.execution_runner import _resolve_agent_payload

    resolved = _resolve_agent_payload(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id='generalist-voice-agent',
            mode='pipecat_webrtc',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
        )
    )

    assert resolved.mode == 'pipecat_webrtc'
    assert resolved.tester_id == 'pipecat_tester'


def test_explicit_text_mode_uses_selected_text_agent_target_when_callable_is_omitted():
    from app.services.execution_runner import _resolve_agent_payload

    created = client.post(
        '/api/agents',
        json={
            'name': 'Explicit live text target',
            'channel': 'text',
            'target': 'openai_codex',
        },
    )
    assert created.status_code == 200

    resolved = _resolve_agent_payload(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            agent_id=created.json()['id'],
            mode='text_callable',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )

    assert resolved.mode == 'text_callable'
    assert resolved.text_callable == 'openai_codex'


def test_execution_rejects_callable_that_does_not_match_selected_target():
    created = client.post(
        '/api/agents',
        json={
            'name': 'HTTP provenance guard',
            'channel': 'text',
            'target': 'http_endpoint',
            'connection': {'endpoint_url': 'https://support.example.test/chat'},
        },
    )
    assert created.status_code == 200, created.text

    with pytest.raises(ValueError, match='would execute a different target'):
        start_execution_run(
            ExecutionRunCreateRequest(
                suite_id='call-center-voice-ai',
                scenario_ids=['billing-address-change'],
                agent_id=created.json()['id'],
                mode='text_callable',
                text_callable='mock_agent',
                user_id='agent-runs-user',
                project_id='agent-runs-project',
            )
        )


def test_rejects_openai_callable_without_agent_before_queueing(monkeypatch):
    created_records = []
    monkeypatch.setattr(
        execution_run_store,
        'create_execution_run',
        lambda record: created_records.append(record),
    )

    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'mode': 'text_callable',
            'text_callable': 'openai_codex',
            'user_id': 'agent-runs-user',
            'project_id': 'agent-runs-project',
        },
    )

    assert response.status_code == 400
    assert response.json()['detail'] == 'openai_codex execution requires an agent_id.'
    assert created_records == []


def test_saved_text_replay_without_scenarios_defaults_to_cancellation_rescue():
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            text_callable='offline_acc_fixture',
            tester_id='scenario_simulator',
            executor_id='local_async_runner',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
        )
    )

    assert queued['mode'] == 'text_callable'
    assert queued['tester_id'] == 'fixture_replay'
    assert queued['executor_id'] == 'evidence_replay'
    assert queued['provenance']['saved_evidence'] is True
    assert queued['scenario_ids'] == ['cancellation-rescue']


@pytest.mark.parametrize(
    ('mode', 'target_kind'),
    [
        ('voice_fixture', 'saved_voice_replay'),
        ('pipecat_webrtc', 'builtin_sample_voice'),
    ],
)
def test_no_agent_voice_run_uses_inferred_target_provenance(mode: str, target_kind: str):
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            mode=mode,
            user_id='agent-runs-user',
            project_id='agent-runs-project',
        )
    )

    assert queued['provenance']['target_kind'] == target_kind
    assert queued['provenance']['target_channel'] == 'voice'


def test_explicit_openai_target_executes_selected_model_for_any_text_agent_without_fake_tool_evidence():
    from app.services.llm_providers import set_provider_for_tests

    class FakeOpenAIProvider:
        provider_id = 'openai'

        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.model_names: list[str | None] = []
            self.target_responses = iter([
                'Please confirm the ZIP code and phone number on the account.',
                'Thanks. What is the new billing address?',
                'I have recorded the requested address details for review.',
            ])
            self.tester_responses = iter([
                'The ZIP is 94107 and the phone ends in 4421.',
                'The new address is 123 Market Street, San Francisco, CA 94105.',
            ])

        def status(self):
            return {'status': 'connected', 'provider': 'openai_codex'}

        def complete(self, prompt: str, *, model_name: str | None = None) -> str:
            self.prompts.append(prompt)
            self.model_names.append(model_name)
            if 'adaptive text-agent evaluation' in prompt:
                return next(self.tester_responses)
            return next(self.target_responses)

    created = client.post(
        '/api/agents',
        json={
            'name': 'Live OpenAI support agent',
            'channel': 'text',
            'target': 'openai_codex',
            'description': 'Verify the caller before account changes.',
        },
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()['id']
    fake = FakeOpenAIProvider()
    set_provider_for_tests('openai', fake)
    try:
        payload = ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            mode='text_callable',
            text_callable='openai_codex',
            agent_id=agent_id,
            model_name='gpt-5.4',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
            evaluate=False,
        )
        queued = start_execution_run(payload)
        assert queued['mode'] == 'text_callable'
        # Queue-time settings remain executable even if a user removes the
        # registry entry before the background worker begins.
        deleted = client.delete(f'/api/agents/{agent_id}')
        assert deleted.status_code == 200
        finished = execute_execution_run(queued['execution_run_id'], payload)
    finally:
        set_provider_for_tests('openai', None)

    assert finished['status'] == 'completed'
    assert finished['max_exchanges'] == 3
    conversation = finished['conversations'][0]
    assert conversation['transcript'].endswith('I have recorded the requested address details for review.')
    assert [turn['speaker'] for turn in conversation['turns']] == [
        'user', 'agent', 'user', 'agent', 'user', 'agent',
    ]
    assert len(conversation['live_events']) == 6
    assert len(conversation['latency_marks']) == 5
    assert conversation['action_trace'] == []
    assert conversation['final_state']['complete'] is False
    assert conversation['final_state']['termination_reason'] == 'max_exchanges'
    assert conversation['final_state']['runtime_provenance']['completed_exchanges'] == 3
    assert conversation['final_state']['runtime_provenance']['fixture_backed'] is False
    assert conversation['final_state']['runtime_provenance']['live_tool_execution'] is False
    assert fake.model_names == ['gpt-5.4'] * 5
    target_prompts = [prompt for prompt in fake.prompts if 'adaptive text-agent evaluation' not in prompt]
    tester_prompts = [prompt for prompt in fake.prompts if 'adaptive text-agent evaluation' in prompt]
    assert len(target_prompts) == 3
    assert len(tester_prompts) == 2
    assert 'Verify the caller before account changes.' in target_prompts[0]
    assert 'Required behavior to cover' not in '\n'.join(target_prompts)
    assert 'verify account using at least two identifiers' not in '\n'.join(target_prompts)
    assert 'Behaviors to probe: greet caller and identify intent' in tester_prompts[0]
    assert 'The ZIP is 94107 and the phone ends in 4421.' in target_prompts[1]
