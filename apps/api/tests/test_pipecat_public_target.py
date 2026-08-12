from __future__ import annotations

import base64
import io
import json
import wave

import pytest

import app.services.pipecat_public_target as public_target_service
from app.services.pipecat_public_target import run_public_pipecat_call
from app.services.reference_generalist_agent import ReferenceRuntimeConfig


def _wav(value: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(bytes([value, 0]) * 320)
    return output.getvalue()


class _StreamResponse:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    def iter_lines(self):
        for event in self.events:
            yield json.dumps(event)


def test_public_target_client_persists_current_run_media_without_room_credentials(tmp_path):
    caller_wav = _wav(1)
    target_wav = _wav(2)
    observed: dict[str, object] = {}
    result_payload = {
        'status': 'pass',
        'target': {'selected_agent': '10-gradium', 'transport': 'pipecat_daily_webrtc'},
        'turns': [
            {'speaker': 'caller', 'text': 'What is Pipecat?'},
            {'speaker': 'agent', 'text': 'Pipecat is a voice AI framework.'},
        ],
        'connection': {'connected': True, 'response_complete': True},
        'latency_metrics': {
            'tester_speech_end_to_first_target_audio_received_ms': 321.5,
            'tester_speech_end_to_first_target_speech_received_ms': 321.5,
        },
        'exchanges': [{
            'turn_pair': 1,
            'latency': {
                'tester_speech_end_to_first_target_audio_received_ms': 321.5,
                'tester_speech_end_to_first_target_speech_received_ms': 321.5,
                'first_target_media_frame_latency_ms': 0.2,
                'signal_boundary': 'silero_vad_speech_onset',
                'response_complete_latency_ms': 900.0,
            },
        }],
        'media': {
            'caller_audio_wav_base64': base64.b64encode(caller_wav).decode(),
            'target_audio_wav_base64': base64.b64encode(target_wav).decode(),
        },
        'provenance': {'daily_room_credentials_persisted': False},
    }

    class FakeClient:
        def stream(self, method, url, *, headers, json):
            observed.update({'method': method, 'url': url, 'headers': headers, 'json': json})
            return _StreamResponse([
                {
                    'type': 'phase',
                    'phase': 'bot_joined',
                    'text': 'Public Pipecat bot joined the Daily room.',
                },
                {
                    'type': 'live_audio',
                    'turn_pair': 1,
                    'speaker': 'Caller',
                    'direction': 'tester_to_target',
                    'text': 'What is Pipecat?',
                    'audio_wav_base64': base64.b64encode(caller_wav).decode(),
                },
                {
                    'type': 'live_transcript',
                    'turn_pair': 1,
                    'speaker': 'Agent',
                    'direction': 'target_to_tester',
                    'text': 'Pipecat is a voice AI framework.',
                    'media_event': 'rtvi_transcript_progress',
                },
                {'type': 'complete', 'result': result_payload},
            ])

    live_events: list[dict[str, object]] = []
    result = run_public_pipecat_call(
        caller_text='What is Pipecat?',
        artifact_dir=tmp_path,
        conversation_id='conversation-1',
        timeout_seconds=60,
        scenario={'id': 'demo', 'goal': 'Ask about Pipecat.'},
        max_exchanges=1,
        event_observer=live_events.append,
        config=ReferenceRuntimeConfig(
            pipecat_service_url='http://pipecat.test',
            internal_token='internal-only',
        ),
        client=FakeClient(),  # type: ignore[arg-type]
    )

    assert observed['method'] == 'POST'
    assert observed['url'] == 'http://pipecat.test/public-pipecat/duplex'
    assert observed['headers'] == {'x-cae-reference-token': 'internal-only'}
    assert observed['json'] == {
        'caller_text': 'What is Pipecat?',
        'agent': '10-gradium',
        'timeout_seconds': 60,
        'scenario': {'id': 'demo', 'goal': 'Ask about Pipecat.'},
        'max_turn_pairs': 1,
        'tester_model_name': None,
        'execution_run_id': None,
        'session_id': 'conversation-1',
    }
    assert live_events[0]['text'] == 'Public Pipecat bot joined the Daily room.'
    assert live_events[0]['frame_metadata']['connection_phase'] == 'bot_joined'
    assert live_events[1]['audio'] == caller_wav
    assert live_events[2]['text'] == 'Pipecat is a voice AI framework.'
    assert 'audio' not in live_events[2]
    assert live_events[2]['update_live_audio_key'] == '1:target_to_tester'
    assert [turn.text for turn in result['transcription_turns']] == [
        'What is Pipecat?',
        'Pipecat is a voice AI framework.',
    ]
    assert (tmp_path / 'conversation-1-caller.wav').read_bytes() == caller_wav
    assert (tmp_path / 'conversation-1-target.wav').read_bytes() == target_wav
    assert result['recording_handle'].transport == 'pipecat_daily_webrtc'
    assert 'dailyRoom' not in str(result)
    assert 'dailyToken' not in str(result)


def test_public_target_client_surfaces_safe_pipecat_failure_detail(tmp_path):
    class FakeClient:
        def stream(self, *_args, **_kwargs):
            return _StreamResponse([{
                'type': 'error',
                'detail': 'Public Pipecat bot joined Daily but did not become ready.',
            }])

    with pytest.raises(
        RuntimeError,
        match='Public Pipecat bot joined Daily but did not become ready',
    ):
        run_public_pipecat_call(
            caller_text='What is Pipecat?',
            artifact_dir=tmp_path,
            conversation_id='conversation-error',
            timeout_seconds=60,
            config=ReferenceRuntimeConfig(
                pipecat_service_url='http://pipecat.test',
                internal_token='internal-only',
            ),
            client=FakeClient(),  # type: ignore[arg-type]
        )


def test_public_target_client_timeout_includes_service_setup_and_playback(monkeypatch, tmp_path):
    observed: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *, timeout):
            observed['timeout'] = timeout

        def stream(self, *_args, **_kwargs):
            return _StreamResponse([{
                'type': 'error',
                'detail': 'Public Pipecat bot did not complete a response before the run timeout.',
            }])

        def close(self):
            observed['closed'] = True

    monkeypatch.setattr(public_target_service.httpx, 'Client', FakeClient)

    with pytest.raises(RuntimeError, match='did not complete a response'):
        run_public_pipecat_call(
            caller_text='What is Pipecat?',
            artifact_dir=tmp_path,
            conversation_id='conversation-timeout-budget',
            timeout_seconds=300,
            max_exchanges=10,
            config=ReferenceRuntimeConfig(
                pipecat_service_url='http://pipecat.test',
                internal_token='internal-only',
            ),
        )

    assert observed == {'timeout': 660, 'closed': True}
