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


def call(tmp_path, *, peer=None, client=None, audio=None, steps=None, **extra):
    async def wording(turns, index):
        return f'caller {index}'
    async def synthesis(text, index):
        return audio[index - 1] if audio else pcm_wav(b'\x01\x00' * 100)
    return asyncio.run(run_waylo_call(target=TARGET, correlation_id='exec-test/conv-test',
        execution_run_id='exec-test', conversation_id='conv-test',
        scenario={'id': 'case', 'caller_steps': steps or ['hello'], **extra}, suite_id='suite',
        artifact_dir=tmp_path, max_exchanges=len(steps or ['hello']), timeout_seconds=30,
        next_utterance=wording, synthesize=synthesis, client=client or Client(), peer=peer or Peer()))


def test_run_vcon_retains_actual_execution_and_conversation_identity(tmp_path):
    result = call(tmp_path)
    context = decode_evidence(result['ietf_vcon_export'])['context']
    assert context['execution_run_id'] == 'exec-test'
    assert context['conversation_id'] == 'conv-test'
    serialized = json.dumps(result['ietf_vcon_export']['analysis'])
    assert 'exec-test' in serialized and 'conv-test' in serialized


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
    call(tmp_path, peer=peer)
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
