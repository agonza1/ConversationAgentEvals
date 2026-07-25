from __future__ import annotations

import asyncio
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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_bytes(self):
        yield self.content


class _AsyncClient:
    completion_prompts: list[str] = []
    speech_voices: list[str] = []

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
            type(self).completion_prompts.append(kwargs['json']['prompt'])
            return _Response(payload={'text': 'Of course.'})
        raise AssertionError(url)

    def stream(self, method, url, **kwargs):
        assert method == 'POST'
        if url.endswith('/v1/audio/speech'):
            type(self).speech_voices.append(kwargs['json']['voice'])
            return _Response(content=_wav())
        raise AssertionError(url)


class _DivergentReceiptAsyncClient:
    completion_prompts: list[str] = []
    transcript_index = 0
    completion_index = 0
    transcripts = [
        'target heard caller receipt one',
        'tester heard agent receipt one',
        'target heard caller receipt two',
        'tester heard agent receipt two',
    ]
    completions = [
        'claimed caller wording one',
        'claimed agent wording one',
        'claimed caller wording two',
        'claimed agent wording two',
    ]

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        if url.endswith('/api/transcribe/file'):
            text = type(self).transcripts[type(self).transcript_index]
            type(self).transcript_index += 1
            return _Response(payload={'text': text})
        if url.endswith('/api/execution/reference/complete'):
            assert kwargs['headers']['x-cae-reference-token'] == 'test-token'
            type(self).completion_prompts.append(kwargs['json']['prompt'])
            text = type(self).completions[type(self).completion_index]
            type(self).completion_index += 1
            return _Response(payload={'text': text})
        raise AssertionError(url)

    def stream(self, method, url, **kwargs):
        assert method == 'POST'
        if url.endswith('/v1/audio/speech'):
            return _Response(content=_wav())
        raise AssertionError(url)


def test_reference_turn_runs_real_pipecat_pipeline(monkeypatch):
    _AsyncClient.completion_prompts.clear()
    _AsyncClient.speech_voices.clear()
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
    assert payload['first_audio_byte_latency_ms'] >= 0
    assert payload['response_complete_latency_ms'] >= payload['first_audio_byte_latency_ms']
    assert base64.b64decode(payload['agent_audio_wav_base64']).startswith(b'RIFF')
    assert 'one or two short sentences' in _AsyncClient.completion_prompts[0]
    assert 'Ask at most one question at a time' in _AsyncClient.completion_prompts[0]
    assert 'Do not use markdown, bullets, or numbered lists' in _AsyncClient.completion_prompts[0]
    assert _AsyncClient.speech_voices == ['am_adam']


def test_reference_tester_turn_runs_real_pipecat_pipeline(monkeypatch):
    _AsyncClient.speech_voices.clear()
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
    assert _AsyncClient.speech_voices == ['af_heart']


def test_reference_duplex_stream_runs_two_graphs_over_local_frames(monkeypatch):
    _AsyncClient.completion_prompts.clear()
    _AsyncClient.speech_voices.clear()
    monkeypatch.setattr(server, 'RTC_ASR_BASE_URL', 'http://rtc-asr.test')
    monkeypatch.setattr(server, 'KOKORO_BASE_URL', 'http://kokoro.test')
    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(server.httpx, 'AsyncClient', _AsyncClient)
    client = TestClient(server.app)

    request = {
        'session_id': 'offline-duplex-proof',
        'execution_run_id': 'offline-execution-proof',
        'scenario': {
            'id': 'billing-address-change',
            'title': 'Billing Address Change',
            'persona': 'A customer who recently moved.',
            'goal': 'Verify the account and update the billing address.',
            'required_actions': ['verify account', 'collect new billing address'],
            'forbidden_actions': ['change address before verification'],
            'expected_final_state': 'The verified account has the new billing address.',
        },
        'tester_model_name': 'tester-model',
        'target_model_name': 'target-model',
        'llm_provider': 'offline-fake-openai',
        'llm_mode': 'mock',
        'max_turn_pairs': 2,
        'total_timeout_seconds': 20,
        'tester_voice': 'af_heart',
        'target_voice': 'am_adam',
    }
    assert 'audio' not in str(request).lower()
    response = client.post(
        '/reference-duplex/run',
        headers={'x-cae-reference-token': 'test-token'},
        json=request,
    )

    assert response.status_code == 200, response.text
    events = [server.json.loads(line) for line in response.text.splitlines() if line.strip()]
    live_audio = [event for event in events if event['type'] == 'live_audio']
    exchanges = [event for event in events if event['type'] == 'exchange']
    completed = events[-1]
    assert len(live_audio) == 4
    assert [event['direction'] for event in live_audio] == [
        'tester_to_target',
        'target_to_tester',
        'tester_to_target',
        'target_to_tester',
    ]
    assert live_audio[0]['speaker'] == 'Caller'
    assert live_audio[1]['speaker'] == 'Agent'
    assert live_audio[0]['audio_wav_base64']
    assert events.index(live_audio[0]) < events.index(exchanges[0])
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
    assert all(frame['duration_ms'] > 0 for frame in completed['frames'])
    assert completed['graphs']['tester']['processors'][1]['model'] == 'tester-model'
    assert completed['graphs']['target']['processors'][1]['model'] == 'target-model'
    assert completed['graphs']['tester']['processors'][2]['voice'] == 'af_heart'
    assert completed['graphs']['target']['processors'][2]['voice'] == 'am_adam'
    assert completed['graphs']['tester']['llm_mode'] == 'mock'
    assert exchanges[0]['target']['asr_receipt'] == 'Please help me.'
    assert exchanges[0]['target']['tester_asr_receipt'] == 'Please help me.'
    assert exchanges[0]['latency_kind'] == 'target_first_audio_byte'
    assert exchanges[0]['latency_ms'] >= 0
    assert exchanges[0]['exchange_elapsed_ms'] >= exchanges[0]['latency_ms']
    assert exchanges[0]['target']['frame']['response_metric'] == 'target_time_to_first_audio_byte'
    target_prompts = [
        prompt for prompt in _AsyncClient.completion_prompts
        if 'built-in generalist voice agent' in prompt
    ]
    assert target_prompts
    assert all('one or two short sentences' in prompt for prompt in target_prompts)
    tester_prompts = [
        prompt for prompt in _AsyncClient.completion_prompts
        if 'caller-side Pipecat tester' in prompt
    ]
    assert tester_prompts
    assert all('Billing Address Change' in prompt for prompt in tester_prompts)
    assert all('update the billing address' in prompt for prompt in tester_prompts)
    assert _AsyncClient.speech_voices == ['af_heart', 'am_adam', 'af_heart', 'am_adam']


def test_reference_duplex_rejects_matching_voices(monkeypatch):
    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    client = TestClient(server.app)
    response = client.post(
        '/reference-duplex/run',
        headers={'x-cae-reference-token': 'test-token'},
        json={
            'session_id': 'matching-voices',
            'execution_run_id': 'matching-voices-run',
            'scenario': {'id': 'billing-address-change', 'title': 'Billing Address Change'},
            'tester_voice': 'af_heart',
            'target_voice': 'af_heart',
        },
    )
    assert response.status_code == 422


def test_reference_duplex_history_uses_peer_asr_receipts_for_later_prompts(monkeypatch):
    _DivergentReceiptAsyncClient.completion_prompts.clear()
    _DivergentReceiptAsyncClient.transcript_index = 0
    _DivergentReceiptAsyncClient.completion_index = 0
    monkeypatch.setattr(server, 'RTC_ASR_BASE_URL', 'http://rtc-asr.test')
    monkeypatch.setattr(server, 'KOKORO_BASE_URL', 'http://kokoro.test')
    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(server.httpx, 'AsyncClient', _DivergentReceiptAsyncClient)
    client = TestClient(server.app)

    response = client.post(
        '/reference-duplex/run',
        headers={'x-cae-reference-token': 'test-token'},
        json={
            'session_id': 'offline-duplex-history-proof',
            'execution_run_id': 'offline-execution-history-proof',
            'scenario': {'id': 'cancellation-rescue', 'title': 'Cancellation Rescue'},
            'tester_model_name': 'tester-model',
            'target_model_name': 'target-model',
            'llm_provider': 'offline-fake-openai',
            'llm_mode': 'mock',
            'max_turn_pairs': 2,
            'total_timeout_seconds': 20,
        },
    )

    assert response.status_code == 200, response.text
    events = [server.json.loads(line) for line in response.text.splitlines() if line.strip()]
    exchanges = [event for event in events if event['type'] == 'exchange']
    assert [exchange['target']['asr_receipt'] for exchange in exchanges] == [
        'target heard caller receipt one',
        'target heard caller receipt two',
    ]
    assert [exchange['target']['tester_asr_receipt'] for exchange in exchanges] == [
        'tester heard agent receipt one',
        'tester heard agent receipt two',
    ]
    target_prompts = [
        prompt for prompt in _DivergentReceiptAsyncClient.completion_prompts
        if 'built-in generalist voice agent' in prompt
    ]
    assert len(target_prompts) == 2
    assert 'Caller: target heard caller receipt one' in target_prompts[1]
    assert 'Agent: tester heard agent receipt one' in target_prompts[1]
    assert 'claimed caller wording one' not in target_prompts[1]
    assert 'claimed agent wording one' not in target_prompts[1]


def test_reference_listener_negotiates_receive_only_webrtc_and_receives_frames(monkeypatch):
    class _Track:
        _sample_rate = 24000

        def __init__(self):
            self.audio = []

        def add_audio_bytes(self, payload):
            self.audio.append(payload)

    class _Connection:
        last = None

        def __init__(self, *args, **kwargs):
            self._presenter_answer_audio_track = _Track()
            self.disconnected = False
            _Connection.last = self

        async def initialize(self, sdp, type):
            assert sdp == 'receive-only-offer'
            assert type == 'offer'

        def get_answer(self):
            return {'sdp': 'send-only-answer', 'type': 'answer', 'pc_id': 'listener-pc'}

        async def connect(self):
            return None

        async def disconnect(self):
            self.disconnected = True

        async def add_ice_candidate(self, candidate):
            return None

    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(server, 'ReferenceListenerWebRTCConnection', _Connection)
    broadcast = server._ReferenceDuplexBroadcast(
        execution_run_id='active-run',
        session_id='active-session',
    )
    server.REFERENCE_DUPLEX_RUNS['active-run'] = broadcast
    client = TestClient(server.app)

    joined = client.post(
        '/reference-duplex/listen',
        headers={'x-cae-reference-token': 'test-token'},
        json={
            'execution_run_id': 'active-run',
            'listener_id': 'owner-listener',
            'sdp': 'receive-only-offer',
            'type': 'offer',
            'expires_at_unix': server.time.time() + 120,
        },
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()['read_only'] is True
    assert joined.json()['requires_microphone'] is False
    assert joined.json()['answer']['sdp'] == 'send-only-answer'

    broadcast.publish(b'\x01\x00' * 240, sample_rate=24000)
    assert _Connection.last._presenter_answer_audio_track.audio

    _Connection.last._presenter_answer_audio_track.audio.clear()
    broadcast.publish(b'\x02\x00' * 160, sample_rate=16000)
    assert len(_Connection.last._presenter_answer_audio_track.audio) == 1
    assert len(_Connection.last._presenter_answer_audio_track.audio[0]) == 480

    _Connection.last._presenter_answer_audio_track.audio.clear()
    stereo_payload = server.struct.pack('<2h', 1000, 3000) * 240
    broadcast.publish(stereo_payload, sample_rate=24000, channels=2)
    assert len(_Connection.last._presenter_answer_audio_track.audio) == 1
    stereo_downmix = _Connection.last._presenter_answer_audio_track.audio[0]
    assert len(stereo_downmix) == 480
    assert server.struct.unpack('<h', stereo_downmix[:2])[0] == 2000

    stopped = client.post(
        '/reference-duplex/listen/stop',
        headers={'x-cae-reference-token': 'test-token'},
        json={'execution_run_id': 'active-run', 'listener_id': 'owner-listener'},
    )
    assert stopped.status_code == 200
    assert _Connection.last.disconnected is True
    server.REFERENCE_DUPLEX_RUNS.pop('active-run', None)


def test_reference_listener_waits_for_duplex_broadcast_registration(monkeypatch):
    class _Track:
        _sample_rate = 24000

        def add_audio_bytes(self, payload):
            return None

    class _Connection:
        def __init__(self, *args, **kwargs):
            self._presenter_answer_audio_track = _Track()

        async def initialize(self, sdp, type):
            assert sdp == 'receive-only-offer'
            assert type == 'offer'

        def get_answer(self):
            return {'sdp': 'send-only-answer', 'type': 'answer'}

        async def connect(self):
            return None

        async def disconnect(self):
            return None

    async def run_join():
        async def register_broadcast():
            await asyncio.sleep(0.02)
            server.REFERENCE_DUPLEX_RUNS['soon-active-run'] = server._ReferenceDuplexBroadcast(
                execution_run_id='soon-active-run',
                session_id='soon-active-session',
            )

        asyncio.create_task(register_broadcast())
        return await server.reference_duplex_listen(
            server.ReferenceListenerJoinRequest(
                execution_run_id='soon-active-run',
                listener_id='owner-listener',
                sdp='receive-only-offer',
                type='offer',
                expires_at_unix=server.time.time() + 120,
            ),
            x_cae_reference_token='test-token',
        )

    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(server, 'REFERENCE_LISTENER_BROADCAST_WAIT_SECONDS', 1.0)
    monkeypatch.setattr(server, 'REFERENCE_LISTENER_BROADCAST_POLL_SECONDS', 0.005)
    monkeypatch.setattr(server, 'ReferenceListenerWebRTCConnection', _Connection)
    server.REFERENCE_DUPLEX_RUNS.pop('soon-active-run', None)

    joined = asyncio.run(run_join())

    assert joined['status'] == 'listening'
    assert joined['answer']['sdp'] == 'send-only-answer'
    server.REFERENCE_DUPLEX_RUNS.pop('soon-active-run', None)
