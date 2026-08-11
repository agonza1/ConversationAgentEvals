import asyncio
import json
from types import SimpleNamespace

import pytest

import public_daily_target
import server
from public_daily_target import (
    PublicDailyTargetError,
    PublicDailyTargetRequest,
    _completed_bot_output_text,
    _message_completes_bot_turn,
    run_public_daily_target,
)


def test_public_target_timeout_matches_execution_api_limit():
    caller_text = 'Please update my billing address.'

    service_request = server.PublicPipecatRunRequest(
        caller_text=caller_text,
        timeout_seconds=300,
    )
    daily_request = PublicDailyTargetRequest(
        caller_text=caller_text,
        timeout_seconds=300,
    )

    assert service_request.timeout_seconds == 300
    assert daily_request.timeout_seconds == 300


def test_completed_bot_output_text_accepts_legacy_spoken_segment():
    assert _completed_bot_output_text({
        'type': 'bot-output',
        'data': {
            'text': 'The completed spoken sentence.',
            'aggregated_by': 'sentence',
            'spoken': True,
        },
    }) == 'The completed spoken sentence.'


def test_completed_bot_output_text_accepts_v2_completed_progress():
    assert _completed_bot_output_text({
        'type': 'bot-output',
        'data': {
            'text': 'The completed spoken sentence.',
            'will_be_spoken': True,
            'spoken_status': 'completed',
            'spoken_progress': {
                'accumulated_text': 'The completed spoken sentence.',
                'remaining_text': '',
            },
        },
    }) == 'The completed spoken sentence.'


def test_completed_bot_output_text_ignores_unspoken_or_in_progress_segments():
    assert _completed_bot_output_text({
        'type': 'bot-output',
        'data': {'text': 'LLM text before TTS.', 'spoken': False},
    }) == ''
    assert _completed_bot_output_text({
        'type': 'bot-output',
        'data': {
            'text': 'Still speaking.',
            'will_be_spoken': True,
            'spoken_status': 'in-progress',
        },
    }) == ''


def test_rtvi_v2_completed_events_finish_bot_turn_without_stopped_speaking():
    assert _message_completes_bot_turn('bot-output', {
        'type': 'bot-output',
        'data': {
            'text': 'Ready to help.',
            'will_be_spoken': True,
            'spoken_status': 'completed',
        },
    }) is True
    assert _message_completes_bot_turn('bot-transcription', {
        'type': 'bot-transcription',
        'data': {'text': 'Ready to help.', 'final': True},
    }) is True
    assert _message_completes_bot_turn('bot-output', {
        'type': 'bot-output',
        'data': {'text': 'Still speaking.', 'will_be_spoken': True, 'spoken_status': 'in-progress'},
    }) is False
    assert _message_completes_bot_turn('bot-transcription', {
        'type': 'bot-transcription',
        'data': {'text': 'A sentence without an explicit completion flag.'},
    }) is False
    assert _message_completes_bot_turn('bot-output', {
        'type': 'bot-output',
        'data': {'text': 'One spoken segment.', 'spoken': True},
    }) is False


def test_remote_audio_latency_waits_for_confirmed_speech(monkeypatch):
    class FakeVad:
        def __init__(self, *_args, **_kwargs):
            self.states = iter([
                public_daily_target.VADState.QUIET,
                public_daily_target.VADState.STARTING,
                public_daily_target.VADState.SPEAKING,
            ])

        def set_sample_rate(self, _sample_rate):
            pass

        async def analyze_audio(self, _audio):
            return next(self.states)

        async def cleanup(self):
            pass

    monkeypatch.setattr(public_daily_target, 'SileroVADAnalyzer', FakeVad)

    async def collect():
        evidence = public_daily_target._DirectDailyEvidence(
            current_turn_pair=1,
            caller_audio_sent_at=public_daily_target.time.perf_counter() - 1,
            caller_audio_ended_at=public_daily_target.time.perf_counter(),
            capture_response_audio=True,
        )
        collector = public_daily_target._RemoteAudioCollector(evidence)
        frame = public_daily_target.UserAudioRawFrame(
            bytes([1, 0]) * 320,
            sample_rate=16_000,
            num_channels=1,
            user_id='remote-bot',
        )
        await collector.process_frame(frame, public_daily_target.FrameDirection.DOWNSTREAM)
        first_media_at = evidence.first_target_audio_at
        assert first_media_at is not None
        assert evidence.first_target_speech_at is None

        await asyncio.sleep(0.025)
        await collector.process_frame(frame, public_daily_target.FrameDirection.DOWNSTREAM)
        assert evidence.first_target_speech_at is None

        await asyncio.sleep(0.025)
        await collector.process_frame(frame, public_daily_target.FrameDirection.DOWNSTREAM)
        assert evidence.first_target_speech_at is not None
        assert evidence.first_target_speech_at > first_media_at
        assert evidence.target_audio_frames == 3
        await collector.cleanup()

    asyncio.run(collect())


def test_public_duplex_reuses_rtvi_text_for_next_tester_turn(monkeypatch):
    observed: dict[str, object] = {}

    async def fake_graph(input_frame, _llm_processor, *, voice):
        observed['tester_input_type'] = type(input_frame).__name__
        observed['tester_input_text'] = input_frame.text
        observed['voice'] = voice
        return SimpleNamespace(transcript=''), SimpleNamespace(
            agent_text='Could you tell me when the cough started?',
            audio=bytes([1, 0]) * 320,
            sample_rate=16_000,
            channels=1,
        )

    async def fake_duplex(_request, **kwargs):
        await kwargs['audio_frame_callback'](
            'target_to_tester', bytes([3, 0]) * 160, 16_000, 1, 1,
        )
        caller_text, caller_wav = await kwargs['next_turn'](
            2,
            'I can provide information, but I cannot book appointments.',
            public_daily_target._pcm_to_wav(bytes([2, 0]) * 320, 16_000, 1),
        )
        observed['caller_text'] = caller_text
        observed['caller_wav'] = caller_wav
        return {'status': 'pass'}

    monkeypatch.setattr(server, '_run_reference_graph', fake_graph)
    monkeypatch.setattr(public_daily_target, 'run_public_daily_duplex', fake_duplex)

    async def collect_events():
        payload = server.PublicPipecatDuplexRequest(
            caller_text='I need a same-day visit.',
            scenario={'id': 'triage', 'goal': 'Request a same-day visit.'},
            max_turn_pairs=2,
            execution_run_id='exec-public-listener',
            session_id='public-session',
        )
        return [json.loads(item) async for item in server._public_pipecat_duplex_events(payload)]

    events = asyncio.run(collect_events())

    assert observed['tester_input_type'] == 'TextFrame'
    assert observed['tester_input_text'] == (
        'I can provide information, but I cannot book appointments.'
    )
    assert observed['caller_text'] == 'Could you tell me when the cough started?'
    assert bytes(observed['caller_wav']).startswith(b'RIFF')
    assert events == [{'type': 'complete', 'result': {'status': 'pass'}}]
    broadcast = server.REFERENCE_DUPLEX_RUNS.pop('exec-public-listener')
    assert broadcast.audio_publish_sequence == 1
    assert broadcast.started_listener_media_keys == {
        'public-session:1:target_to_tester',
    }
    assert broadcast.active is False


def test_public_target_reports_tester_audio_synthesis_stage(monkeypatch):
    async def fail_synthesis(*_args, **_kwargs):
        raise OSError('connection refused')

    monkeypatch.setattr(public_daily_target, '_synthesize_caller', fail_synthesis)

    with pytest.raises(
        PublicDailyTargetError,
        match='tester audio synthesis failed; verify Kokoro is reachable',
    ):
        asyncio.run(run_public_daily_target(
            PublicDailyTargetRequest(caller_text='Please update my billing address.'),
            kokoro_base_url='http://kokoro.invalid',
            kokoro_model='kokoro',
            kokoro_voice='af_heart',
        ))
