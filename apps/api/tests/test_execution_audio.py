from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.execution import ExecutionRunCreateRequest
from app.services.acc_realtime_target import AccAudioFixture, AccAudioFixtureScheduler, AccAudioPlan, AccAudioStep
from app.services.execution_audio import (
    LocalPipecatSmallWebRtcSession,
    SipVertoOutboundExtension,
    WebRtcBackedVoiceTarget,
    create_execution_audio_session,
    describe_execution_audio_capabilities,
    pcm_silence_frames,
    pcm_tone_frames,
)
from app.services.execution_run_store import reset_execution_runs_for_tests


client = TestClient(app)


def setup_function() -> None:
    reset_execution_runs_for_tests()


def test_capabilities_advertise_local_webrtc_and_deferred_sip():
    payload = describe_execution_audio_capabilities()
    ids = {item['id'] for item in payload.transports}
    assert ids >= {'none', 'local_pipecat_webrtc', 'sip_verto'}
    local = next(item for item in payload.transports if item['id'] == 'local_pipecat_webrtc')
    sip = next(item for item in payload.transports if item['id'] == 'sip_verto')
    assert local['available'] is True
    assert local['requires_freeswitch'] is False
    assert sip['available'] is False
    assert sip['status'] == 'deferred'
    assert payload.freeswitch_required is False


def test_audio_capabilities_endpoint():
    response = client.get('/api/execution/audio/capabilities')
    assert response.status_code == 200
    body = response.json()
    assert body['default_transport'] == 'none'
    assert any(item['id'] == 'local_pipecat_webrtc' for item in body['transports'])


def test_execution_health_includes_audio_capabilities():
    response = client.get('/api/execution/health')
    assert response.status_code == 200
    assert response.json()['ok'] is True
    assert 'audio' in response.json()


def test_local_smallwebrtc_session_streams_in_and_out():
    async def run() -> None:
        session = LocalPipecatSmallWebRtcSession(metadata={'scenario_id': 'cancellation-rescue'})
        negotiated = await session.negotiate()
        assert negotiated['transport'] == 'local_pipecat_webrtc'
        assert negotiated['offer']['type'] == 'offer'
        assert negotiated['answer']['type'] == 'answer'

        async def outbound():
            for frame in pcm_silence_frames(duration_ms=40):
                yield frame

        sent = await session.send_pcm(outbound())
        assert sent['frame_count'] >= 2
        assert sent['audio_bytes'] > 0

        async def inbound():
            for frame in pcm_tone_frames(duration_ms=20):
                yield frame

        remote = await session.inject_remote_pcm(inbound())
        assert remote['frame_count'] >= 1
        received = await session.receive_pcm()
        assert len(received) == remote['frame_count']
        assert all(frame.direction == 'target_to_caller' for frame in received)

        proof = await session.close(reason='unit_test')
        assert proof.negotiated is True
        assert proof.closed is True
        assert proof.frames_sent == sent['frame_count']
        assert proof.frames_received == len(received)
        assert proof.provider == 'pipecat.smallwebrtc.local'

    asyncio.run(run())


def test_webrtc_backed_target_works_with_fixture_scheduler():
    async def run() -> None:
        session = create_execution_audio_session(
            'local_pipecat_webrtc',
            metadata={'source': 'unit-test'},
        )
        await session.negotiate()
        target = WebRtcBackedVoiceTarget(session)
        scheduler = AccAudioFixtureScheduler(target, sleeper=_noop_sleep, event_poll_interval_seconds=0.01)
        plan = AccAudioPlan(
            scenario_id='cancellation-rescue',
            seed=7,
            provenance={'source': 'unit-test'},
            fixtures=[
                AccAudioFixture(
                    fixture_id='cancel-v1',
                    uri='fixture://cancel.wav',
                    expected_caller_act='request_cancellation',
                    duration_ms=40,
                    metadata={'text_reference': 'I want to cancel'},
                )
            ],
            steps=[
                AccAudioStep(
                    step_id='step-1',
                    fixture_id='cancel-v1',
                    expected_caller_act='request_cancellation',
                    pacing_mode='accelerated',
                    acceleration_factor=4.0,
                )
            ],
        )
        results = await scheduler.run(session.session_id, plan)
        assert len(results) == 1
        assert results[0]['response']['audio']['send']['frame_count'] >= 1
        assert results[0]['response']['audio']['received_frames'] >= 1
        proof = await session.close()
        assert proof.frames_sent >= 1
        assert proof.frames_received >= 1
        assert len(target.media_steps) == 1

    asyncio.run(run())


def test_sip_verto_extension_is_deferred():
    extension = SipVertoOutboundExtension()
    caps = extension.capabilities()
    assert caps['status'] == 'deferred'
    assert caps['available'] is False
    with pytest.raises(NotImplementedError):
        asyncio.run(extension.create_session())


def test_create_session_rejects_sip_verto():
    with pytest.raises(NotImplementedError):
        create_execution_audio_session('sip_verto')


def test_schema_rejects_sip_verto_and_text_with_audio():
    with pytest.raises(ValidationError):
        ExecutionRunCreateRequest(
            mode='voice_webrtc',
            audio_transport='sip_verto',
            scenario_ids=['cancellation-rescue'],
        )
    with pytest.raises(ValidationError):
        ExecutionRunCreateRequest(
            mode='text_callable',
            audio_transport='local_pipecat_webrtc',
            scenario_ids=['billing-address-change'],
        )


def test_voice_webrtc_defaults_transport_to_local():
    payload = ExecutionRunCreateRequest(
        mode='voice_webrtc',
        scenario_ids=['cancellation-rescue'],
    )
    assert payload.audio_transport == 'local_pipecat_webrtc'


def test_voice_webrtc_execution_attaches_audio_session_proof():
    queued = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'mode': 'voice_webrtc',
            'iterations': 1,
            'user_id': 'webrtc-user',
            'project_id': 'webrtc-project',
            'evaluate': True,
        },
    )
    assert queued.status_code == 200, queued.text
    run_id = queued.json()['execution_run_id']
    assert queued.json()['mode'] == 'voice_webrtc'

    completed = _wait_for_terminal(run_id, user_id='webrtc-user')
    assert completed['status'] in {'completed', 'needs_review', 'failed'}
    conversation = completed['conversations'][0]
    assert conversation['scenario_id'] == 'cancellation-rescue'
    assert conversation['status'] == 'completed'
    assert conversation.get('audio_session')
    audio = conversation['audio_session']
    assert audio['transport'] == 'local_pipecat_webrtc'
    assert audio['provider'] == 'pipecat.smallwebrtc.local'
    assert audio['negotiated'] is True
    assert audio['closed'] is True
    assert audio['frames_sent'] >= 1
    assert audio['frames_received'] >= 1
    assert audio['media_steps']
    assert audio['extension_points']['sip_verto']['status'] == 'deferred'
    assert any('webrtc_audio_streamed' in (turn.get('event_types') or []) for turn in conversation['turns'])


def test_voice_fixture_can_opt_into_local_webrtc_transport():
    queued = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'mode': 'voice_fixture',
            'audio_transport': 'local_pipecat_webrtc',
            'iterations': 1,
            'user_id': 'fixture-webrtc-user',
            'project_id': 'fixture-webrtc-project',
            'evaluate': False,
        },
    )
    assert queued.status_code == 200, queued.text
    completed = _wait_for_terminal(queued.json()['execution_run_id'], user_id='fixture-webrtc-user')
    conversation = completed['conversations'][0]
    assert conversation['audio_session']['transport'] == 'local_pipecat_webrtc'
    assert conversation['audio_session']['frames_sent'] >= 1


def test_sip_verto_transport_rejected_at_api():
    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'mode': 'voice_fixture',
            'audio_transport': 'sip_verto',
            'user_id': 'sip-user',
            'project_id': 'sip-project',
        },
    )
    assert response.status_code == 422


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


async def _noop_sleep(_seconds: float) -> None:
    return None
