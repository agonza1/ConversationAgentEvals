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
    DeterministicExecutionTtsRenderer,
    ExecutionAudioTargetAdapter,
    FreeSwitchVertoSipTransport,
    LocalPipecatSmallWebRtcTransport,
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
    assert ids >= {'none', 'pipecat_small_webrtc', 'freeswitch_verto_sip'}
    local = next(item for item in payload.transports if item.id == 'pipecat_small_webrtc')
    sip = next(item for item in payload.transports if item.id == 'freeswitch_verto_sip')
    assert local.available is True
    assert local.requires_freeswitch is False
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


def test_execution_health_includes_audio_capabilities():
    response = client.get('/api/execution/health')
    assert response.status_code == 200
    assert response.json()['ok'] is True
    assert response.json()['audio']['vcon_capture'] is True


def test_pipecat_webrtc_mode_defaults_transport_and_rejects_verto():
    ok = ExecutionRunCreateRequest(
        mode='pipecat_webrtc',
        scenario_ids=['cancellation-rescue'],
        user_id='u',
        project_id='p',
    )
    assert ok.audio_transport == 'pipecat_small_webrtc'

    with pytest.raises(ValidationError) as exc:
        ExecutionRunCreateRequest(
            mode='pipecat_webrtc',
            audio_transport='freeswitch_verto_sip',
            scenario_ids=['cancellation-rescue'],
            user_id='u',
            project_id='p',
        )
    assert 'deferred' in str(exc.value)


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
        assert exported['attachments']
        assert exported['attachments'][0]['type'] == 'recording'
        assert exported['attachments'][0]['url'] == recording.uri
        summary = vcon_summary(exported)
        assert summary['available'] is True
        assert summary['recording_attached'] is True
        assert summary['dialog_turns'] >= 2

    asyncio.run(run())


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


def test_pipecat_webrtc_execution_run_emits_vcon_without_live_sip():
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
    assert queued.status_code == 200, queued.text
    run_id = queued.json()['execution_run_id']
    assert queued.json()['mode'] == 'pipecat_webrtc'

    completed = _wait_for_terminal(run_id, user_id='webrtc-user')
    assert completed['status'] in {'completed', 'needs_review', 'failed'}
    assert len(completed['conversations']) == 1
    conversation = completed['conversations'][0]
    assert conversation['scenario_id'] == 'cancellation-rescue'
    assert conversation['turns']
    assert any(turn.get('speaker') == 'caller' for turn in conversation['turns'])
    assert conversation.get('recording', {}).get('recording_url')
    vcon = conversation.get('vcon_export') or {}
    assert vcon.get('vcon') == '0.0.1'
    assert vcon.get('source_format') == 'pipecat_execution'
    assert vcon.get('appended_analysis_type') == 'execution_audio_capture'
    assert isinstance(vcon.get('dialog'), list) and len(vcon['dialog']) >= 2
    assert conversation.get('vcon_export_summary', {}).get('recording_attached') is True
    assert conversation.get('audio_session', {}).get('extension_points', {}).get('freeswitch_verto_sip', {}).get(
        'status'
    ) == 'deferred'


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
