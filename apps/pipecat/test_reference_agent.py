from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import struct
import time
import wave
from types import SimpleNamespace

from fastapi.testclient import TestClient

import server


def test_browser_mdns_ice_candidate_uses_configured_host_gateway(monkeypatch):
    monkeypatch.setattr(server, 'BROWSER_ICE_HOST_OVERRIDE', 'host.docker.internal')

    candidate = server._coerce_ice_candidate({
        'candidate': (
            'candidate:1 1 udp 2113937151 '
            'browser-session.local 63556 typ host generation 0'
        ),
        'sdpMid': '0',
        'sdpMLineIndex': 0,
    })

    assert candidate.ip == 'host.docker.internal'
    assert candidate.port == 63556
    assert candidate.sdpMid == '0'
    assert candidate.sdpMLineIndex == 0


def test_transient_reference_completion_errors_are_retryable():
    assert server._is_transient_reference_completion_error(
        RuntimeError(
            'Codex Responses request failed (503): upstream connect error or disconnect/reset'
        )
    )
    assert not server._is_transient_reference_completion_error(
        RuntimeError('Codex Responses request failed (403): forbidden')
    )


def test_streaming_completion_retries_before_first_delta(monkeypatch):
    class _StreamResponse:
        def __init__(self, lines):
            self.lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in self.lines:
                yield line

    class _Client:
        def __init__(self):
            self.calls = 0

        def stream(self, *args, **kwargs):
            del args, kwargs
            self.calls += 1
            if self.calls == 1:
                return _StreamResponse([
                    '{"type":"error","detail":"Codex Responses request failed (503): '
                    'connection termination"}',
                ])
            return _StreamResponse([
                '{"type":"delta","text":"Recovered"}',
                '{"type":"completed","text":"Recovered","ttft_ms":50,"total_ms":75}',
            ])

    class _Processor:
        def __init__(self):
            self.client = _Client()
            self.model_name = 'fake-model'
            self.frames = []

        async def start_processing_metrics(self):
            return None

        async def start_ttfb_metrics(self):
            return None

        async def stop_ttfb_metrics(self):
            return None

        async def stop_processing_metrics(self):
            return None

        async def push_frame(self, frame, direction):
            self.frames.append((frame, direction))

    async def no_sleep(_seconds):
        return None

    async def run():
        processor = _Processor()
        monkeypatch.setattr(server.asyncio, 'sleep', no_sleep)
        await server._stream_reference_completion(
            processor=processor,
            prompt='hello',
            direction=server.FrameDirection.DOWNSTREAM,
            start_frame=server._TesterLlmStartFrame(),
            text_frame_type=server._TesterSpeechFrame,
            end_frame=server._TesterLlmEndFrame(),
        )
        return processor

    processor = asyncio.run(run())

    assert processor.client.calls == 2
    assert processor.text == 'Recovered'
    assert processor.provider_ttft_ms == 50.0
    assert [type(frame) for frame, _ in processor.frames] == [
        server._TesterLlmStartFrame,
        server._TesterSpeechFrame,
        server._TesterLlmEndFrame,
    ]


def test_turn_completion_collector_fails_active_turn_on_pipeline_error():
    async def run():
        collector = server._TurnCompletionCollector()
        future = collector.begin_turn()
        collector.fail('upstream failed')
        return future

    future = asyncio.run(run())

    assert future.done()
    try:
        future.result()
    except RuntimeError as exc:
        assert str(exc) == 'Pipecat pipeline failed: upstream failed'
    else:
        raise AssertionError('Expected the pipeline error to fail the turn future.')


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b'\x01\x00' * 160)
    return output.getvalue()


def _streaming_result(
    *,
    caller_text: str,
    target_receipt: str,
    target_text: str,
    tester_receipt: str,
):
    pcm = b'\x01\x00' * 160
    speech_ended_at = time.time()
    return SimpleNamespace(
        tester_llm=SimpleNamespace(text=caller_text),
        caller_tts=SimpleNamespace(
            audio=bytearray(pcm),
            sample_rate=16000,
            channels=1,
        ),
        target_asr=SimpleNamespace(
            transcript=target_receipt,
            interims=['partial caller'],
            server_timing={'revision': 2},
            speech_ended_at=speech_ended_at,
            final_at=speech_ended_at + 0.01,
        ),
        target_llm=SimpleNamespace(
            text=target_text,
            ttft_ms=5.0,
            total_ms=7.0,
        ),
        target_tts=SimpleNamespace(
            audio=bytearray(pcm),
            sample_rate=16000,
            channels=1,
            ttfb_ms=3.0,
            total_ms=4.0,
        ),
        tester_asr=SimpleNamespace(
            transcript=tester_receipt,
            interims=['partial target'],
            server_timing={'revision': 2},
        ),
        metrics=[{'processor': 'target_llm', 'value': 0.005}],
        tester_speech_ended_at=speech_ended_at,
        target_audio_received_at=speech_ended_at,
        target_first_audio_latency_ms=0.0,
        target_response_complete_latency_ms=10.0,
    )


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
    assert _AsyncClient.speech_voices == ['af_bella']


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


def test_reference_duplex_stream_emits_streaming_graph_evidence(monkeypatch):
    _AsyncClient.completion_prompts.clear()
    _AsyncClient.speech_voices.clear()
    monkeypatch.setattr(server, 'RTC_ASR_BASE_URL', 'http://rtc-asr.test')
    monkeypatch.setattr(server, 'KOKORO_BASE_URL', 'http://kokoro.test')
    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(server.httpx, 'AsyncClient', _AsyncClient)

    async def fake_exchange(**kwargs):
        payload = kwargs['payload']
        history = kwargs['history']
        event_callback = kwargs['event_callback']
        _AsyncClient.completion_prompts.extend([
            (
                'caller-side Pipecat tester\n'
                f'{payload.scenario["title"]}\n'
                f'{payload.scenario["goal"]}'
            ),
            (
                'built-in generalist voice agent\n'
                'one or two short sentences\n'
                + '\n'.join(f'{item["speaker"]}: {item["text"]}' for item in history)
            ),
        ])
        _AsyncClient.speech_voices.extend([payload.tester_voice, payload.target_voice])
        await event_callback({
            'type': 'speech_started',
            'participant': 'tester',
            'speaker': 'Caller',
            'direction': 'tester_to_target',
            'text': 'Of course.',
            'llm_output': 'Of course.',
            'first_audible_pcm_at': 10.0,
        })
        await event_callback({
            'type': 'speech_started',
            'participant': 'target',
            'speaker': 'Agent',
            'direction': 'target_to_tester',
            'text': 'Of course.',
            'llm_output': 'Of course.',
            'first_audible_pcm_at': 11.0,
        })
        return _streaming_result(
            caller_text='Of course.',
            target_receipt='Please help me.',
            target_text='Of course.',
            tester_receipt='Please help me.',
        )

    monkeypatch.setattr(server, '_run_streaming_exchange', fake_exchange)
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
        'stt_backend': 'parakeet-mlx',
        'stt_model': 'mlx-community/parakeet-tdt_ctc-110m',
        'max_turn_pairs': 2,
        'total_timeout_seconds': 20,
        'tester_voice': 'af_heart',
        'target_voice': 'af_bella',
    }
    assert 'audio' not in str(request).lower()
    response = client.post(
        '/reference-duplex/run',
        headers={'x-cae-reference-token': 'test-token'},
        json=request,
    )

    assert response.status_code == 200, response.text
    events = [server.json.loads(line) for line in response.text.splitlines() if line.strip()]
    speech_started = [event for event in events if event['type'] == 'speech_started']
    live_audio = [event for event in events if event['type'] == 'live_audio']
    exchanges = [event for event in events if event['type'] == 'exchange']
    completed = events[-1]
    assert len(live_audio) == 4
    assert len(speech_started) == 4
    assert events.index(speech_started[0]) < events.index(speech_started[1])
    assert events.index(speech_started[1]) < events.index(live_audio[0])
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
    assert completed['architecture'] == 'persistent_streaming_pipecat_duplex_local_stt_v1'
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
    assert completed['graphs']['tester']['processors'][0]['backend'] == 'parakeet-mlx'
    assert completed['graphs']['target']['processors'][0]['model'] == (
        'mlx-community/parakeet-tdt_ctc-110m'
    )
    assert completed['graphs']['tester']['processors'][2]['voice'] == 'af_heart'
    assert completed['graphs']['target']['processors'][2]['voice'] == 'af_bella'
    assert completed['graphs']['tester']['llm_mode'] == 'mock'
    assert exchanges[0]['target']['asr_receipt'] == 'Please help me.'
    assert exchanges[0]['target']['tester_asr_receipt'] == 'Please help me.'
    assert exchanges[0]['latency_kind'] == 'tester_speech_end_to_first_target_audio_received'
    assert exchanges[0]['latency_ms'] >= 0
    assert exchanges[0]['exchange_elapsed_ms'] >= exchanges[0]['latency_ms']
    assert exchanges[0]['target']['frame']['response_metric'] == (
        'tester_speech_end_to_first_target_audio_received'
    )
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
    assert _AsyncClient.speech_voices == ['af_heart', 'af_bella', 'af_heart', 'af_bella']


def test_reference_duplex_cancels_active_exchange_when_stream_closes(monkeypatch):
    cancelled = asyncio.Event()

    async def blocking_exchange(**kwargs):
        await kwargs['event_callback']({'type': 'vad', 'state': 'speaking'})
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def retire_immediately(_broadcast):
        return None

    async def run():
        stream = server._reference_duplex_events(server.ReferenceDuplexRunRequest(
            session_id='disconnect-session',
            execution_run_id='disconnect-run',
            scenario={'id': 'billing-address-change', 'title': 'Billing Address Change'},
            llm_mode='mock',
            max_turn_pairs=1,
            total_timeout_seconds=20,
        ))
        first_event = server.json.loads((await anext(stream)).decode())
        assert first_event['type'] == 'vad'
        await stream.aclose()
        assert cancelled.is_set()

    monkeypatch.setattr(server, '_run_streaming_exchange', blocking_exchange)
    monkeypatch.setattr(server, '_retire_reference_broadcast', retire_immediately)

    asyncio.run(run())


def test_reference_duplex_migrates_suite_listener_to_next_conversation(monkeypatch):
    class _Track:
        _sample_rate = 24000

        def __init__(self):
            self.audio = []

        def add_audio_bytes(self, payload):
            self.audio.append(payload)

    class _Connection:
        def __init__(self):
            self.disconnected = False

        async def disconnect(self):
            self.disconnected = True

    track = _Track()
    connection = _Connection()
    prior = server._ReferenceDuplexBroadcast(
        execution_run_id='suite-run',
        session_id='suite-conversation-1',
        active=False,
        listeners={
            'suite-listener': server._ReferenceListener(
                listener_id='suite-listener',
                connection=connection,
                track=track,
            ),
        },
    )
    server.REFERENCE_DUPLEX_RUNS['suite-run'] = prior

    async def blocking_exchange(**kwargs):
        await kwargs['event_callback']({'type': 'vad', 'state': 'speaking'})
        await asyncio.Event().wait()

    async def retire_immediately(_broadcast):
        return None

    async def run():
        stream = server._reference_duplex_events(server.ReferenceDuplexRunRequest(
            session_id='suite-conversation-2',
            execution_run_id='suite-run',
            scenario={'id': 'second-scenario', 'title': 'Second Scenario'},
            llm_mode='mock',
            max_turn_pairs=1,
            total_timeout_seconds=20,
        ))
        await anext(stream)
        current = server.REFERENCE_DUPLEX_RUNS['suite-run']
        assert current is not prior
        assert not prior.listeners
        assert current.listeners['suite-listener'].connection is connection
        current.publish(b'\x01\x00' * 240, sample_rate=24000)
        assert track.audio

        await stream.aclose()
        await server._expire_reference_listener(
            'suite-run',
            'suite-listener',
            server.time.time() - 1,
        )
        assert connection.disconnected is True
        assert 'suite-run' not in server.REFERENCE_DUPLEX_RUNS

    monkeypatch.setattr(server, '_run_streaming_exchange', blocking_exchange)
    monkeypatch.setattr(server, '_retire_reference_broadcast', retire_immediately)

    asyncio.run(run())


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

    async def fake_exchange(**kwargs):
        history = kwargs['history']
        index = kwargs['turn_index'] - 1
        caller_text = _DivergentReceiptAsyncClient.completions[index * 2]
        target_text = _DivergentReceiptAsyncClient.completions[index * 2 + 1]
        target_receipt = _DivergentReceiptAsyncClient.transcripts[index * 2]
        tester_receipt = _DivergentReceiptAsyncClient.transcripts[index * 2 + 1]
        _DivergentReceiptAsyncClient.completion_prompts.extend([
            (
                'caller-side Pipecat tester\n'
                + '\n'.join(f'{item["speaker"]}: {item["text"]}' for item in history)
            ),
            (
                'built-in generalist voice agent\n'
                + '\n'.join(f'{item["speaker"]}: {item["text"]}' for item in history)
                + f'\nCaller: {target_receipt}'
            ),
        ])
        return _streaming_result(
            caller_text=caller_text,
            target_receipt=target_receipt,
            target_text=target_text,
            tester_receipt=tester_receipt,
        )

    monkeypatch.setattr(server, '_run_streaming_exchange', fake_exchange)
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
        last_kwargs = None

        def __init__(self, *args, **kwargs):
            self._presenter_answer_audio_track = _Track()
            self.disconnected = False
            _Connection.last = self
            _Connection.last_kwargs = kwargs

        async def initialize(self, sdp, type):
            assert sdp == 'receive-only-offer'
            assert type == 'offer'
            broadcast.publish(b'\x00\x00' * 240, sample_rate=24000)

        def get_answer(self):
            return {'sdp': 'send-only-answer', 'type': 'answer', 'pc_id': 'listener-pc'}

        async def connect(self):
            return None

        async def disconnect(self):
            self.disconnected = True

        async def add_ice_candidate(self, candidate):
            return None

    monkeypatch.setattr(server, 'REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setattr(server, 'LISTENER_TURN_URL', 'turn:coturn:3478?transport=udp')
    monkeypatch.setattr(server, 'LISTENER_TURN_USERNAME', 'cae')
    monkeypatch.setattr(server, 'LISTENER_TURN_CREDENTIAL', 'local-secret')
    monkeypatch.setattr(server, 'LISTENER_TURN_SHARED_SECRET', 'rest-auth-secret')
    monkeypatch.setattr(server, 'ReferenceListenerWebRTCConnection', _Connection)
    broadcast = server._ReferenceDuplexBroadcast(
        execution_run_id='active-run',
        session_id='active-session',
    )
    server.REFERENCE_DUPLEX_RUNS['active-run'] = broadcast
    client = TestClient(server.app)

    expires_at_unix = server.time.time() + 120
    joined = client.post(
        '/reference-duplex/listen',
        headers={'x-cae-reference-token': 'test-token'},
        json={
            'execution_run_id': 'active-run',
            'listener_id': 'owner-listener',
            'sdp': 'receive-only-offer',
            'type': 'offer',
            'expires_at_unix': expires_at_unix,
        },
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()['read_only'] is True
    assert joined.json()['requires_microphone'] is False
    assert joined.json()['audio_published_during_attach'] is True
    assert joined.json()['answer']['sdp'] == 'send-only-answer'
    turn_server = _Connection.last_kwargs['ice_servers'][0]
    assert turn_server.urls == 'turn:coturn:3478?transport=udp'
    expected_username = f'{int(expires_at_unix)}:cae:owner-listener'
    expected_credential = base64.b64encode(hmac.new(
        b'rest-auth-secret',
        expected_username.encode(),
        hashlib.sha1,
    ).digest()).decode()
    assert turn_server.username == expected_username
    assert turn_server.credential == expected_credential

    broadcast.publish(b'\x01\x00' * 240, sample_rate=24000)
    assert _Connection.last._presenter_answer_audio_track.audio

    _Connection.last._presenter_answer_audio_track.audio.clear()
    broadcast.publish(b'\x02\x00' * 160, sample_rate=16000)
    assert len(_Connection.last._presenter_answer_audio_track.audio) == 1
    assert len(_Connection.last._presenter_answer_audio_track.audio[0]) == 480

    _Connection.last._presenter_answer_audio_track.audio.clear()
    stereo_payload = struct.pack('<2h', 1000, 3000) * 240
    broadcast.publish(stereo_payload, sample_rate=24000, channels=2)
    assert len(_Connection.last._presenter_answer_audio_track.audio) == 1
    stereo_downmix = _Connection.last._presenter_answer_audio_track.audio[0]
    assert len(stereo_downmix) == 480
    assert struct.unpack('<h', stereo_downmix[:2])[0] == 2000

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
