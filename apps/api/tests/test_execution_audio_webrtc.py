from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.execution import ExecutionRunCreateRequest
from app.services.acc_realtime_target import AccAudioFixture, AccAudioStep
from app.services.execution_audio import (
    AudioRecordingHandle,
    DeterministicExecutionTtsRenderer,
    ExecutionAudioTargetAdapter,
    FreeSwitchVertoSipTransport,
    LocalPipecatSmallWebRtcTransport,
    TranscriptionTurn,
    describe_execution_audio_capabilities,
)
from app.services.execution_run_store import reset_execution_runs_for_tests
from app.services.execution_vcon import build_execution_vcon, vcon_summary
from app.services.pipecat_tester_agent import PipecatTesterAgentRunner
from app.services.acc_realtime_target import (
    TesterAct as CallerAct,
    TesterScenarioConfig as CallerScenarioConfig,
)


client = TestClient(app)


def setup_function() -> None:
    reset_execution_runs_for_tests()


def test_capabilities_advertise_local_webrtc_vcon_and_deferred_sip():
    payload = describe_execution_audio_capabilities()
    ids = {item.id for item in payload.transports}
    assert ids >= {
        'none',
        'pipecat_small_webrtc',
        'pipecat_daily_webrtc',
        'freeswitch_verto_sip',
    }
    local = next(item for item in payload.transports if item.id == 'pipecat_small_webrtc')
    daily = next(item for item in payload.transports if item.id == 'pipecat_daily_webrtc')
    sip = next(item for item in payload.transports if item.id == 'freeswitch_verto_sip')
    assert local.available is True
    assert local.requires_freeswitch is False
    assert daily.available is True
    assert daily.requires_live_pipecat is True
    assert daily.requires_freeswitch is False
    assert sip.available is False
    assert sip.status == 'deferred'
    assert payload.freeswitch_required is False
    assert payload.vcon_capture is True


def test_audio_capabilities_endpoint():
    response = client.get('/api/execution/audio/capabilities')
    assert response.status_code == 200
    body = response.json()
    assert body['default_transport'] == 'none'
    assert body['vcon_capture'] is True
    assert any(item['id'] == 'pipecat_small_webrtc' for item in body['transports'])
    assert any(item['id'] == 'pipecat_daily_webrtc' for item in body['transports'])


def test_execution_health_includes_audio_capabilities():
    response = client.get('/api/execution/health')
    assert response.status_code == 200
    assert response.json()['ok'] is True
    assert response.json()['audio']['vcon_capture'] is True
    preflight = response.json()['reference_voice']
    assert preflight['llm_mode'] == 'real'
    assert {item['id'] for item in preflight['dependencies']} == {
        'llm', 'shared_token', 'pipecat', 'rtc_asr', 'kokoro'
    }
    assert all(item['detail'] for item in preflight['dependencies'])
    assert all(item['setup_url'].startswith('https://') for item in preflight['dependencies'])
    assert next(item for item in preflight['dependencies'] if item['id'] == 'rtc_asr')['setup_url'] == (
        'https://github.com/agonza1/rtc-asr'
    )
    assert next(item for item in preflight['dependencies'] if item['id'] == 'kokoro')['setup_url'] == (
        'https://github.com/remsky/Kokoro-FastAPI'
    )


def test_reference_voice_preflight_adopts_rtc_asr_backend_and_model(monkeypatch):
    import app.routes.execution as execution_routes

    class _Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, **_kwargs):
        if url.endswith('/reference-agent/readiness'):
            return _Response({
                'ready': True,
                'duplex_route_ready': True,
                'listener_webrtc_ready': True,
            })
        if url == 'http://rtc-asr.test/health':
            return _Response({'backend': 'mlx-parakeet', 'model': 'parakeet-tdt'})
        if url == 'http://kokoro.test/health':
            return _Response({'status': 'ready'})
        raise AssertionError(url)

    monkeypatch.setenv('OPENAI_API_KEY', 'offline-status-only')
    monkeypatch.setenv('REFERENCE_AGENT_INTERNAL_TOKEN', 'test-token')
    monkeypatch.setenv('PIPECAT_SERVICE_URL', 'http://pipecat.test')
    monkeypatch.setenv('RTC_ASR_BASE_URL', 'http://rtc-asr.test')
    monkeypatch.setenv('RTC_ASR_HEALTH_PATH', '/health')
    monkeypatch.setenv('REFERENCE_STT_BACKEND', 'whisper')
    monkeypatch.setenv('KOKORO_BASE_URL', 'http://kokoro.test')
    monkeypatch.setattr(execution_routes.httpx, 'get', fake_get)

    report = execution_routes._reference_voice_preflight()
    rtc_asr = next(item for item in report['dependencies'] if item['id'] == 'rtc_asr')
    assert report['ready'] is True
    assert rtc_asr['ready'] is True
    assert rtc_asr['detail'] == (
        'Reachable at http://rtc-asr.test; using '
        'mlx-parakeet (parakeet-tdt) selected by rtc-asr.'
    )


def test_pipecat_webrtc_mode_defaults_transport_and_rejects_verto():
    ok = ExecutionRunCreateRequest(
        mode='pipecat_webrtc',
        tester_id='pipecat_tester',
        executor_id='local_async_runner',
        scenario_ids=['cancellation-rescue'],
        user_id='u',
        project_id='p',
    )
    assert ok.audio_transport == 'pipecat_small_webrtc'
    assert ok.executor_id == 'cae_local_audio_loop'

    with pytest.raises(ValidationError) as exc:
        ExecutionRunCreateRequest(
            mode='pipecat_webrtc',
            audio_transport='freeswitch_verto_sip',
            scenario_ids=['cancellation-rescue'],
            user_id='u',
            project_id='p',
        )
    assert 'deferred' in str(exc.value)

    with pytest.raises(ValidationError) as voice_exc:
        ExecutionRunCreateRequest(
            mode='voice_fixture',
            audio_transport='pipecat_small_webrtc',
            scenario_ids=['cancellation-rescue'],
            user_id='u',
            project_id='p',
        )
    assert 'voice_fixture' in str(voice_exc.value)


def test_execution_request_defaults_and_bounds_max_exchanges():
    default_request = ExecutionRunCreateRequest()
    assert default_request.max_exchanges == 3
    assert default_request.duplex_timeout_seconds == 120

    for invalid in (0, 11):
        with pytest.raises(ValidationError):
            ExecutionRunCreateRequest(max_exchanges=invalid)
    for invalid in (29, 301):
        with pytest.raises(ValidationError):
            ExecutionRunCreateRequest(duplex_timeout_seconds=invalid)


def test_local_webrtc_transport_send_receive_records_and_transcribes():
    async def run() -> None:
        transport = LocalPipecatSmallWebRtcTransport()
        connected = await transport.connect('sess-1', metadata={'scenario_id': 'cancellation-rescue'})
        assert connected['webrtc']['sendrecv'] is True

        fixture = AccAudioFixture(
            fixture_id='caller-1',
            uri='fixture://caller-1.wav',
            expected_caller_act='request_cancellation',
            metadata={'rendered_text': 'I want to cancel my policy today.'},
        )
        step = AccAudioStep(
            step_id='step-1',
            fixture_id='caller-1',
            expected_caller_act='request_cancellation',
        )
        sent = await transport.send_audio(
            'sess-1',
            fixture=fixture,
            step=step,
            provenance={'seed': 1},
        )
        assert sent['accepted'] is True
        received = await transport.receive_audio('sess-1')
        assert received['agent_text']
        assert received['frames']

        closed = await transport.disconnect('sess-1', reason='unit_test')
        assert closed['closed'] is True
        recording = transport.recording_handle('sess-1')
        assert recording is not None
        assert recording.uri
        assert recording.sha256
        turns = transport.transcription_turns('sess-1')
        assert [turn.speaker for turn in turns] == ['Caller', 'Agent']
        assert turns[0].text.startswith('I want to cancel')

    asyncio.run(run())


def test_local_webrtc_transport_session_proof_counts_frames():
    async def run() -> None:
        transport = LocalPipecatSmallWebRtcTransport()
        await transport.connect('sess-proof')
        fixture = AccAudioFixture(
            fixture_id='caller-1',
            uri='fixture://caller-1.wav',
            expected_caller_act='request_cancellation',
            metadata={'rendered_text': 'Cancel today.'},
        )
        step = AccAudioStep(
            step_id='step-1',
            fixture_id='caller-1',
            expected_caller_act='request_cancellation',
        )
        await transport.send_audio('sess-proof', fixture=fixture, step=step, provenance={})
        await transport.receive_audio('sess-proof')
        proof = transport.session_proof('sess-proof')
        assert proof['frames_sent'] >= 1
        assert proof['frames_received'] >= 1
        assert proof['bytes_sent'] > 0
        assert proof['negotiated'] is True

    asyncio.run(run())


def test_capabilities_include_honesty_notes():
    payload = describe_execution_audio_capabilities()
    assert any('FreeSWITCH' in note for note in payload.notes)
    assert any('pipecat_webrtc' in note for note in payload.notes)


def test_verto_transport_is_explicit_extension_stub():
    transport = FreeSwitchVertoSipTransport(verto_url='wss://fs.example/verto', sip_destination='sip:bot@example')
    with pytest.raises(NotImplementedError) as exc:
        asyncio.run(transport.connect('sess-x'))
    assert 'deferred' in str(exc.value).lower()
    assert 'execution-audio-webrtc' in str(exc.value)


def test_build_execution_vcon_reuses_cae_dialog_and_recording_shape():
    async def run() -> None:
        transport = LocalPipecatSmallWebRtcTransport()
        await transport.connect('sess-vcon')
        fixture = AccAudioFixture(
            fixture_id='tts-1',
            uri='fixture://tts-1.wav',
            expected_caller_act='request_cancellation',
            metadata={'rendered_text': 'Please cancel today.'},
        )
        step = AccAudioStep(
            step_id='t1',
            fixture_id='tts-1',
            expected_caller_act='request_cancellation',
        )
        await transport.send_audio('sess-vcon', fixture=fixture, step=step, provenance={})
        await transport.receive_audio('sess-vcon')
        await transport.disconnect('sess-vcon')
        recording = transport.recording_handle('sess-vcon')
        assert recording is not None
        exported = build_execution_vcon(
            conversation_id='c1',
            execution_run_id='exec-1',
            suite_id='call-center-voice-ai',
            scenario_id='cancellation-rescue',
            transport='pipecat_small_webrtc',
            transcription_turns=transport.transcription_turns('sess-vcon'),
            recording=recording,
            termination_reason='plan_complete',
        )
        assert exported['vcon'] == '0.0.1'
        assert exported['source_format'] == 'pipecat_execution'
        assert exported['appended_analysis_type'] == 'execution_audio_capture'
        assert len(exported['dialog']) >= 2
        assert exported['dialog'][0]['source'] == 'pipecat_execution'
        assert exported['attachments']
        assert exported['attachments'][0]['type'] == 'recording'
        assert exported['attachments'][0]['url'] == recording.uri
        summary = vcon_summary(exported)
        assert summary['available'] is True
        assert summary['recording_attached'] is True
        assert summary['dialog_turns'] >= 2

    asyncio.run(run())


def test_build_execution_vcon_preserves_dict_directional_evidence():
    exported = build_execution_vcon(
        conversation_id='c-direction',
        execution_run_id='exec-direction',
        suite_id='call-center-voice-ai',
        scenario_id='cancellation-rescue',
        transport='pipecat_small_webrtc',
        transcription_turns=[
            {
                'speaker': 'tester',
                'text': 'I need to cancel.',
                'source': 'tester.llm_output',
                'direction': 'tester_to_target',
                'evidence_role': 'llm_output',
                'frame_metadata': {'sequence': 1, 'bytes': 123},
            },
            {
                'speaker': 'target',
                'text': 'I need to cancel.',
                'source': 'target.asr_receipt',
                'direction': 'tester_to_target',
                'evidence_role': 'asr_receipt',
                'frame_metadata': {'sequence': 1, 'bytes': 123},
            },
        ],
        recording=None,
    )

    assert exported['source_format'] == 'pipecat_execution'
    assert exported['dialog'][0]['source'] == 'tester.llm_output'
    assert exported['dialog'][0]['direction'] == 'tester_to_target'
    assert exported['dialog'][1]['evidence_role'] == 'asr_receipt'
    assert exported['dialog'][1]['frame_metadata'] == {'sequence': 1, 'bytes': 123}


def test_pipecat_tester_over_execution_audio_target_captures_proof():
    async def run() -> None:
        transport = LocalPipecatSmallWebRtcTransport()
        target = ExecutionAudioTargetAdapter(transport)
        runner = PipecatTesterAgentRunner(
            target=target,
            tts_renderer=DeterministicExecutionTtsRenderer(),
        )
        config = CallerScenarioConfig(
            scenario_id='cancellation-rescue',
            goal='capture vcon',
            allowed_caller_acts=['request_cancellation', 'request_final_disposition'],
            acts=[
                CallerAct(
                    act_id='request_cancellation',
                    objective='cancel',
                    example_utterance='Cancel my policy.',
                ),
                CallerAct(
                    act_id='request_final_disposition',
                    objective='close',
                    example_utterance='Close the call.',
                    terminal_after=True,
                ),
            ],
            max_turns=2,
            total_timeout_seconds=20,
            seed=11,
            observation_mode='semantic',
        )
        result = await runner.run(config)
        assert result['status'] == 'completed'
        assert result['session_id']
        assert result['proof']['recording']
        assert result['proof']['transcription_turns']
        assert len(result['turns']) == 2

    asyncio.run(run())


def test_pipecat_webrtc_execution_fails_closed_without_reference_services(monkeypatch):
    import app.services.execution_runner as execution_runner

    class _ConnectedProvider:
        provider_id = 'fake'

        def status(self):
            return {'status': 'connected', 'provider': 'fake'}

    monkeypatch.setattr(execution_runner, 'resolve_reference_completion_provider', lambda *_args: _ConnectedProvider())
    monkeypatch.delenv('RTC_ASR_BASE_URL', raising=False)
    monkeypatch.delenv('KOKORO_BASE_URL', raising=False)
    queued = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'mode': 'pipecat_webrtc',
            'iterations': 1,
            'user_id': 'webrtc-user',
            'project_id': 'webrtc-project',
            'evaluate': True,
        },
    )
    assert queued.status_code == 400, queued.text
    assert 'RTC_ASR_BASE_URL' in queued.json()['detail']


def test_pipecat_webrtc_propagates_tester_needs_review(monkeypatch, tmp_path):
    import app.services.execution_runner as execution_runner

    class _FakeMedia:
        def synthesize(self, text):
            return b'fake-wav'

    class _FakeReferenceTransport(LocalPipecatSmallWebRtcTransport):
        def __init__(self, **kwargs):
            super().__init__(artifact_dir=tmp_path)
            self.media = _FakeMedia()
            self.runtime = {}

        def latency_marks(self, session_id):
            return []

        async def run_duplex_session(
            self,
            session_id,
            *,
            scenario,
            max_turn_pairs,
            total_timeout_seconds,
        ):
            assert scenario['id'] == 'cancellation-rescue'
            assert max_turn_pairs == 3
            assert total_timeout_seconds == 180
            return {
                'scenario_id': scenario['id'],
                'session_id': session_id,
                'status': 'needs_review',
                'termination_reason': 'runner_error',
                'error': 'injected tester failure',
                'tester_provenance': {},
                'turns': [],
                'proof': {},
            }

    class _FakeCompletion:
        provider_id = 'fake'

        def status(self):
            return {'status': 'connected'}

    monkeypatch.setattr(execution_runner, 'ReferencePipecatAgentTransport', _FakeReferenceTransport)
    monkeypatch.setattr(execution_runner, 'resolve_reference_completion_provider', lambda *_args: _FakeCompletion())
    def _turns(self, session_id):  # noqa: ANN001
        return [
            TranscriptionTurn(turn_index=1, speaker='Caller', text='hi', act_id='a'),
            TranscriptionTurn(turn_index=2, speaker='Agent', text='ok', act_id='b'),
        ]

    def _recording(self, session_id):  # noqa: ANN001
        return AudioRecordingHandle(uri='memory://test.wav', sha256='abc', duration_ms=10)

    def _proof(self, session_id):  # noqa: ANN001
        return {'session_id': session_id, 'frames_sent': 1, 'frames_received': 1}

    monkeypatch.setattr(_FakeReferenceTransport, 'transcription_turns', _turns)
    monkeypatch.setattr(_FakeReferenceTransport, 'recording_handle', _recording)
    monkeypatch.setattr(_FakeReferenceTransport, 'session_proof', _proof)

    queued = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'mode': 'pipecat_webrtc',
            'iterations': 1,
            'user_id': 'webrtc-fail-user',
            'project_id': 'webrtc-project',
            'duplex_timeout_seconds': 180,
            'evaluate': True,
        },
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()['duplex_timeout_seconds'] == 180
    completed = _wait_for_terminal(queued.json()['execution_run_id'], user_id='webrtc-fail-user')
    assert completed['status'] == 'needs_review'
    conversation = completed['conversations'][0]
    assert conversation['verdict'] == 'needs_review'
    assert conversation.get('audio_session', {}).get('tester_error') == 'injected tester failure'
    assert conversation.get('vcon_export', {}).get('vcon') == '0.0.1'


def _wait_for_terminal(run_id: str, *, user_id: str, timeout_seconds: float = 20.0) -> dict:
    deadline = time.time() + timeout_seconds
    latest: dict = {}
    while time.time() < deadline:
        response = client.get(f'/api/execution/runs/{run_id}', params={'user_id': user_id})
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest.get('status') in {'completed', 'needs_review', 'failed'}:
            return latest
        time.sleep(0.05)
    raise AssertionError(f'execution run did not finish: {latest}')
