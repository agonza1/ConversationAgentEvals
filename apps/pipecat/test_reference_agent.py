from __future__ import annotations

import base64
import io
import wave

from fastapi.testclient import TestClient

import server


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b'\x01\x00' * 160)
    return output.getvalue()


class _Response:
    def __init__(self, *, payload=None, content=b'', status_code=200):
        self._payload = payload or {}
        self.content = content
        self.status_code = status_code
        self.text = ''

    def json(self):
        return self._payload

    def raise_for_status(self):
        assert self.status_code < 400


class _AsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        if url.endswith('/api/transcribe/file'):
            return _Response(payload={'text': 'Please help me.'})
        if url.endswith('/api/execution/reference/complete'):
            assert kwargs['headers']['x-cae-reference-token'] == 'test-token'
            return _Response(payload={'text': 'Of course.'})
        if url.endswith('/v1/audio/speech'):
            return _Response(content=_wav())
        raise AssertionError(url)


def test_reference_turn_runs_real_pipecat_pipeline(monkeypatch):
    monkeypatch.setattr(server, 'RTC_ASR_BASE_URL', 'http://rtc-asr.test')
    monkeypatch.setattr(server, 'KOKORO_BASE_URL', 'http://kokoro.test')
    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(server.httpx, 'AsyncClient', _AsyncClient)
    client = TestClient(server.app)
    response = client.post(
        '/reference-agent/turn',
        headers={'x-cae-reference-token': 'test-token'},
        json={'audio_wav_base64': base64.b64encode(_wav()).decode('ascii'), 'history': []},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['caller_transcript'] == 'Please help me.'
    assert payload['agent_text'] == 'Of course.'
    assert payload['pipeline']['provider'] == 'pipecat'
    assert payload['pipeline']['processors'] == ['rtc-asr', 'llm', 'kokoro']
    assert base64.b64decode(payload['agent_audio_wav_base64']).startswith(b'RIFF')


def test_reference_tester_turn_runs_real_pipecat_pipeline(monkeypatch):
    monkeypatch.setattr(server, 'RTC_ASR_BASE_URL', 'http://rtc-asr.test')
    monkeypatch.setattr(server, 'KOKORO_BASE_URL', 'http://kokoro.test')
    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(server.httpx, 'AsyncClient', _AsyncClient)
    client = TestClient(server.app)

    response = client.post(
        '/reference-tester/turn',
        headers={'x-cae-reference-token': 'test-token'},
        json={
            'scenario_instruction': 'Test cancellation rescue.',
            'act_id': 'request_cancellation',
            'act_objective': 'Ask to cancel.',
            'example_utterance': 'Please cancel.',
            'target_audio_wav_base64': base64.b64encode(_wav()).decode('ascii'),
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['tester_asr_receipt'] == 'Please help me.'
    assert payload['tester_text'] == 'Of course.'
    assert payload['pipeline']['processors'] == ['rtc-asr', 'llm', 'kokoro']
    assert base64.b64decode(payload['tester_audio_wav_base64']).startswith(b'RIFF')


def test_reference_duplex_stream_runs_two_graphs_over_local_frames(monkeypatch):
    monkeypatch.setattr(server, 'RTC_ASR_BASE_URL', 'http://rtc-asr.test')
    monkeypatch.setattr(server, 'KOKORO_BASE_URL', 'http://kokoro.test')
    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(server.httpx, 'AsyncClient', _AsyncClient)
    client = TestClient(server.app)

    request = {
        'session_id': 'offline-duplex-proof',
        'scenario': {
            'id': 'cancellation-rescue',
            'title': 'Cancellation Rescue',
            'persona': 'A policyholder who wants to cancel.',
            'goal': 'Reach a safe disposition.',
            'required_actions': ['detect cancellation intent', 'record final disposition'],
            'forbidden_actions': ['make unapproved retention offer'],
            'expected_final_state': 'A safe disposition is recorded.',
        },
        'tester_model_name': 'tester-model',
        'target_model_name': 'target-model',
        'llm_provider': 'offline-fake-openai',
        'llm_mode': 'mock',
        'max_turn_pairs': 2,
        'total_timeout_seconds': 20,
    }
    assert 'audio' not in str(request).lower()
    response = client.post(
        '/reference-duplex/run',
        headers={'x-cae-reference-token': 'test-token'},
        json=request,
    )

    assert response.status_code == 200, response.text
    events = [server.json.loads(line) for line in response.text.splitlines() if line.strip()]
    exchanges = [event for event in events if event['type'] == 'exchange']
    completed = events[-1]
    assert len(exchanges) == 2
    assert completed['type'] == 'complete'
    assert completed['architecture'] == 'two_independent_pipecat_graphs_in_process_duplex_frames'
    assert [frame['direction'] for frame in completed['frames']] == [
        'tester_to_target',
        'target_to_tester',
        'tester_to_target',
        'target_to_tester',
    ]
    assert all(frame['transport'] == 'in_process_pipecat_frame_bus' for frame in completed['frames'])
    assert completed['graphs']['tester']['processors'][1]['model'] == 'tester-model'
    assert completed['graphs']['target']['processors'][1]['model'] == 'target-model'
    assert completed['graphs']['tester']['llm_mode'] == 'mock'
    assert exchanges[0]['target']['asr_receipt'] == 'Please help me.'
    assert exchanges[0]['target']['tester_asr_receipt'] == 'Please help me.'
