import asyncio
import io
import json
import time
import wave
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import execution_runner, waylo_livekit, execution_run_store as runs
from app.services.waylo_target import WayloError, project_capture
from app.services.waylo_livekit import pcm_wav, read_wav, run_waylo_call
from app.services.vcon_evidence import decode_evidence
from app.schemas.execution import ExecutionRunCreateRequest
from test_waylo_target import TARGET, S, captured, transcript


class Client:
    redaction_values = ()
    agent = 'agent'
    def __init__(self):
        self.bootstraps = 0
    async def bootstrap(self, correlation):
        self.bootstraps += 1
        return {'sessionId': S, 'participantToken': 'ephemeral', 'roomName': 'room'}
    async def request(self, method, path):
        return []
    async def capture(self, session_id, origin):
        return captured(origin=origin)
    async def close(self):
        pass


class Peer:
    def __init__(self):
        self.remote_pcm = bytearray()
        self.remote_chunks = []
        self.events, self.segments = [], {}
        self.speech_count = 0
        self.error = None
        self.origin_utc = '2026-10-09T12:00:00Z'
        self.sent = []
    async def connect(self, bootstrap):
        pass
    async def close(self):
        pass
    def now_ms(self):
        return 1000
    async def wait_response(self, previous, timeout=25):
        if timeout == 5:
            raise WayloError('No greeting')
        self.remote_pcm.extend(b'\x03\x00' * 100)
        self.speech_count += 1
        self.segments[('remote', 'reply')] = 'response'
    async def send(self, audio, *, turn_id, reference, artifact_dir):
        self.sent.append(turn_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        event = {'event_id': f'{turn_id}/sent', 'event_type': 'tester.audio.sent',
            'source': 'livekit.AudioSource',
            'turn_id': turn_id, 'reference_text': reference, 'duration_ms': 20,
            'intended_samples': 480, 'accepted_samples': 480, 'audio_sha256': 'digest',
            'ended_at_ms': 100, 'overlap_with_observed_target_speech': False}
        self.events.append(event)
        return event, audio


@pytest.fixture(autouse=True)
def fast_capture_poll(monkeypatch):
    async def no_sleep(seconds):
        pass
    monkeypatch.setattr(waylo_livekit.asyncio, 'sleep', no_sleep)


def call(tmp_path, *, peer=None, client=None, audio=None, steps=None, max_exchanges=None, **extra):
    async def wording(turns, index):
        return f'caller {index}'
    async def synthesis(text, index):
        return audio[index - 1] if audio else pcm_wav(b'\x01\x00' * 100)
    return asyncio.run(run_waylo_call(target=TARGET, correlation_id='exec-test/conv-test',
        execution_run_id='exec-test', conversation_id='conv-test',
        scenario={'id': 'case', 'caller_steps': steps or ['hello'], **extra}, suite_id='suite',
        artifact_dir=tmp_path, max_exchanges=max_exchanges or len(steps or ['hello']), timeout_seconds=30,
        next_utterance=wording, synthesize=synthesis, client=client or Client(), peer=peer or Peer()))


def test_run_vcon_retains_actual_execution_and_conversation_identity(tmp_path):
    result = call(tmp_path)
    context = decode_evidence(result['ietf_vcon_export'])['context']
    assert context['execution_run_id'] == 'exec-test'
    assert context['conversation_id'] == 'conv-test'
    serialized = json.dumps(result['ietf_vcon_export']['analysis'])
    assert 'exec-test' in serialized and 'conv-test' in serialized


def test_late_continuous_microphone_failure_is_not_reported_as_success(tmp_path):
    class LateFailure(Peer):
        continuous_caller = SimpleNamespace(error=None, accepted_samples=0)
        async def close(self):
            self.continuous_caller.error = 'Continuous caller audio publication failed; accepted audio retained.'
    result = call(tmp_path, peer=LateFailure())
    assert result['verdict'] == 'failed'
    assert result['final_state']['tester_error'] == 'Continuous caller audio publication failed; accepted audio retained.'


def test_active_greeting_must_finish_before_first_caller(tmp_path):
    class Greeting(Peer):
        async def wait_response(self, previous, timeout=25):
            if timeout == 5:
                self.speech_count = 1
                raise WayloError('Greeting is still speaking')
            assert not self.sent
            raise WayloError('Greeting never finished')
    peer = Greeting()
    result = call(tmp_path, peer=peer)
    assert not peer.sent
    assert result['final_state']['tester_error'] == 'Greeting never finished'
    assert result['verdict'] == 'failed'
    assert result['ietf_vcon_export'] and result['waylo_capture']


@pytest.mark.parametrize('failure', ['send', 'response'])
def test_rtc_failure_keeps_partial_evidence_but_fails_execution(tmp_path, failure):
    class FailedPeer(Peer):
        async def send(self, *args, **kwargs):
            if failure == 'send':
                raise WayloError('Caller audio send failed')
            return await super().send(*args, **kwargs)
        async def wait_response(self, previous, timeout=25):
            if timeout != 5:
                raise WayloError('No target response')
            await super().wait_response(previous, timeout=timeout)
    result = call(tmp_path, peer=FailedPeer())
    assert result['verdict'] == 'failed'
    assert result['final_state']['tester_error']
    assert result['ietf_vcon_export'] and result['waylo_capture']
    assert result['score'] is None and result['evaluation_report'] == {}


def test_failed_rtc_result_becomes_failed_conversation_with_evidence(tmp_path, monkeypatch):
    class FailedPeer(Peer):
        async def send(self, *args, **kwargs):
            raise WayloError('Caller audio send failed')
    result = call(tmp_path, peer=FailedPeer())
    async def execute(**kwargs):
        return result
    monkeypatch.setattr(execution_runner, '_execute_waylo', execute)
    monkeypatch.setattr(execution_runner, 'get_suite', lambda ident: {'id': ident, 'scenarios': [{'id': 'case', 'title': 'Case'}]})
    monkeypatch.setattr(runs, 'get_conversation', lambda *args: None)
    monkeypatch.setattr(execution_runner, '_live_event_publisher', lambda **kwargs: lambda event: None)
    record = execution_runner._run_one_conversation(execution_run_id='exec-test', suite_id='suite',
        scenario_id='case', iteration=1,
        payload=ExecutionRunCreateRequest(mode='pipecat_webrtc', executor_id='waylo_livekit'), agent_snapshot=TARGET)
    assert record.status == 'failed' and record.verdict == 'failed'
    assert record.error == 'Caller audio send failed'
    assert record.ietf_vcon_export and record.final_state['tester_error']


def test_long_greeting_waits_for_boundary_then_sends(tmp_path):
    class Greeting(Peer):
        finished = False
        async def wait_response(self, previous, timeout=25):
            if timeout == 5:
                self.speech_count = 1
                raise WayloError('Greeting in progress')
            self.finished = True
            await super().wait_response(previous, timeout=timeout)
        async def send(self, *args, **kwargs):
            assert self.finished
            return await super().send(*args, **kwargs)
    peer = Greeting()
    result = call(tmp_path, peer=peer)
    assert result['verdict'] == 'needs_review'
    assert peer.sent == ['caller-1']


def test_barge_in_keeps_response_tail_in_original_overlapping_window(tmp_path):
    class Barge(Peer):
        async def send(self, *args, **kwargs):
            result = await super().send(*args, **kwargs)
            self.remote_pcm.extend((b'\x01\x00' if len(self.sent) == 1 else b'\x02\x00') * 100)
            self.speech_count += 1
            self.last_speech = time.monotonic()
            return result
    result = call(tmp_path, peer=Barge(), steps=['first', 'correction'], waylo_test={'barge_in_turn': 2})
    assert [t['speaker'] for t in result['turns']] == ['Caller', 'Caller', 'Agent']
    assert read_wav((tmp_path / 'agent-1-received.wav').read_bytes())[1] == b'\x01\x00' * 100 + b'\x02\x00' * 100 + b'\x03\x00' * 100
    assert not (tmp_path / 'agent-2-received.wav').exists()
    events = decode_evidence(result['ietf_vcon_export'])['voice_events']
    received = next(e for e in events if e['event_type'] == 'target.audio.received')
    assert received['turn_id'] == 'agent-1'
    assert received['response_to_turn_ids'] == ['caller-1', 'caller-2']
    assert received['individual_response_attribution'] == 'unknown'
    assert not any(e['event_type'] == 'latency.observation' for e in events)


@pytest.mark.parametrize('cap', [1, 2, 3])
def test_interruption_only_runs_when_next_caller_is_within_exchange_cap(tmp_path, cap):
    from app.services.waylo_mike_cases import WAYLO_MIKE_SUITE
    scenario = next(s for s in WAYLO_MIKE_SUITE['scenarios'] if s['id'] == 'mike-interrupted-list')

    class Barge(Peer):
        async def send(self, *args, **kwargs):
            result = await super().send(*args, **kwargs)
            self.remote_pcm.extend(b'\x01\x00' * 100)
            self.speech_count += 1
            self.last_speech = time.monotonic()
            return result

    class ItemClient(Client):
        def __init__(self):
            super().__init__()
            self.item_reads = 0

        async def request(self, method, path):
            if path.endswith('/items'):
                self.item_reads += 1
            return []

    peer, client = Barge(), ItemClient()
    result = call(tmp_path, peer=peer, client=client, max_exchanges=cap,
                  steps=scenario['caller_steps'], waylo_test=scenario['waylo_test'])
    assert result['verdict'] == 'needs_review'
    assert not result['final_state'].get('tester_error')
    assert peer.sent == [f'caller-{n}' for n in range(1, cap + 1)]
    events = decode_evidence(result['ietf_vcon_export'])['voice_events']
    scheduled = [e for e in events if e['event_type'] == 'tester.barge_in_scheduled']
    received = [e for e in events if e['event_type'] == 'target.audio.received']
    assert len(scheduled) == (1 if cap >= 2 else 0)
    assert received[0]['response_to_turn_ids'] == (['caller-1', 'caller-2'] if cap >= 2 else ['caller-1'])
    assert received[0]['individual_response_attribution'] == ('unknown' if cap >= 2 else 'sequential_observation')
    if cap == 1:
        assert [t['speaker'] for t in result['turns']] == ['Caller', 'Agent']
        assert client.item_reads == 2  # Before call and after the final sequential response.


def stereo_wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b'\x00\x10' * 100)
    return output.getvalue()


@pytest.mark.parametrize('bad', [b'corrupt', stereo_wav(), pcm_wav(b''), pcm_wav(b'\x00\x10' * 100)[:-20]])
@pytest.mark.parametrize('index', [0, 1])
def test_invalid_fixed_audio_never_creates_a_remote_session(tmp_path, bad, index):
    client = Client()
    audio = [pcm_wav(b'\x00\x10' * 100)] * 2
    audio[index] = bad
    with pytest.raises(WayloError, match='complete, nonempty mono'):
        call(tmp_path, client=client, audio=audio, steps=['first', 'second'])
    assert client.bootstraps == 0


@pytest.mark.parametrize('control', [{'delay_after_previous_ms': 1}, {'wait_for_event': 'audio'},
    {'wait_timeout_seconds': 10}, {'pacing_mode': 'accelerated'}, {'barge_in': True}])
def test_unsupported_audio_plan_controls_fail_before_provider_use(tmp_path, monkeypatch, control):
    plan = {'scenario_id': 'case', 'seed': 1, 'fixtures': [{'fixture_id': 'f', 'uri': 'not-read',
        'expected_caller_act': 'ask', 'metadata': {'reference_text': 'hello'}}],
        'steps': [{'step_id': 's', 'fixture_id': 'f', 'expected_caller_act': 'ask', **control}]}
    file = tmp_path / 'plan.json'
    file.write_text(json.dumps(plan))
    monkeypatch.setattr(execution_runner, '_repo_path', lambda path: file)
    monkeypatch.setattr(execution_runner, '_scenario_definition', lambda *args: {'id': 'case'})
    monkeypatch.setattr(execution_runner, 'ReferenceMediaServices', lambda *args: pytest.fail('Must not start media'))
    payload = ExecutionRunCreateRequest(agent_id='mike', audio_plan_path='plan.json')
    with pytest.raises(ValueError, match='default realtime'):
        asyncio.run(execution_runner._execute_waylo(execution_run_id='exec-test', conversation_id='conv-test',
            suite_id='suite', scenario_id='case', payload=payload, agent_snapshot=TARGET, event_observer=None))


def test_audio_plan_scenario_mismatch_fails_before_reading_media(tmp_path, monkeypatch):
    file = tmp_path / 'plan.json'
    file.write_text(json.dumps({'scenario_id': 'different', 'seed': 1, 'fixtures': [], 'steps': []}))
    monkeypatch.setattr(execution_runner, '_repo_path', lambda path: file)
    monkeypatch.setattr(execution_runner, '_scenario_definition', lambda *args: {'id': 'case'})
    payload = ExecutionRunCreateRequest(agent_id='mike', audio_plan_path='plan.json')
    with pytest.raises(ValueError, match='scenario_id must match'):
        asyncio.run(execution_runner._execute_waylo(execution_run_id='exec-test', conversation_id='conv-test',
            suite_id='suite', scenario_id='case', payload=payload, agent_snapshot=TARGET, event_observer=None))


def test_selected_dev_origin_enforced_exactly(monkeypatch):
    monkeypatch.setenv('CAE_WAYLO_ALLOWED_ORIGINS', 'http://127.0.0.1:3013,http://localhost:3013')
    client = TestClient(app)
    for origin, status in [('http://localhost:3013', 401), ('http://127.0.0.1:3013', 401),
        ('http://localhost:3012', 403), ('http://localhost:3014', 403), ('https://evil.test', 403)]:
        result = client.get('/api/waylo/connection/status', headers={'Origin': origin, 'X-CAE-Waylo-Control': '1'})
        assert result.status_code == status


def test_one_exchange_exact_opener_does_not_require_an_unrelated_llm_login(tmp_path, monkeypatch):
    from app.services import waylo_target
    monkeypatch.setattr(execution_runner, '_scenario_definition', lambda *args: {'id': 'case', 'title': 'Product information only'})
    monkeypatch.setattr(execution_runner, 'resolve_reference_completion_provider', lambda *args: pytest.fail('No generated continuation needed'))
    monkeypatch.setattr(execution_runner, 'ReferenceMediaServices', lambda *args: SimpleNamespace(client=SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(waylo_target, 'WayloClient', lambda *args, **kwargs: Client())
    async def fake_call(**kwargs):
        assert kwargs['max_exchanges'] == 1
        assert await kwargs['next_utterance']([], 1) == 'Hi, I need help with product information only.'
        return {'ietf_vcon_export': project_capture(captured())['vcon'], 'final_state': {}}
    monkeypatch.setattr(waylo_livekit, 'run_waylo_call', fake_call)
    result = asyncio.run(execution_runner._execute_waylo(execution_run_id='exec-test', conversation_id='conv-test',
        suite_id='suite', scenario_id='case', payload=ExecutionRunCreateRequest(agent_id='mike', max_exchanges=1),
        agent_snapshot=TARGET, event_observer=None))
    assert 'action_trace' in result
