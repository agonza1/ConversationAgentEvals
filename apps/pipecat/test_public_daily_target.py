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
