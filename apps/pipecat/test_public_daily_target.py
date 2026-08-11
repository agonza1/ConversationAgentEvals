import asyncio

import pytest

import public_daily_target
import server
from public_daily_target import (
    PublicDailyTargetError,
    PublicDailyTargetRequest,
    _completed_bot_output_text,
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
