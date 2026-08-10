from __future__ import annotations

import base64
import io
import wave

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


def test_public_target_client_persists_current_run_media_without_room_credentials(tmp_path):
    caller_wav = _wav(1)
    target_wav = _wav(2)
    observed: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                'status': 'pass',
                'target': {'selected_agent': '09-cascade-d', 'transport': 'pipecat_daily_webrtc'},
                'turns': [
                    {'speaker': 'caller', 'text': 'What is Pipecat?'},
                    {'speaker': 'agent', 'text': 'Pipecat is a voice AI framework.'},
                ],
                'connection': {'connected': True, 'response_complete': True},
                'latency_metrics': {'caller_audio_to_first_target_audio_ms': 321.5},
                'media': {
                    'caller_audio_wav_base64': base64.b64encode(caller_wav).decode(),
                    'target_audio_wav_base64': base64.b64encode(target_wav).decode(),
                },
                'provenance': {'daily_room_credentials_persisted': False},
            }

    class FakeClient:
        def post(self, url, *, headers, json):
            observed.update({'url': url, 'headers': headers, 'json': json})
            return FakeResponse()

    result = run_public_pipecat_call(
        caller_text='What is Pipecat?',
        artifact_dir=tmp_path,
        conversation_id='conversation-1',
        timeout_seconds=60,
        config=ReferenceRuntimeConfig(
            pipecat_service_url='http://pipecat.test',
            internal_token='internal-only',
        ),
        client=FakeClient(),  # type: ignore[arg-type]
    )

    assert observed['url'] == 'http://pipecat.test/public-pipecat/run'
    assert observed['headers'] == {'x-cae-reference-token': 'internal-only'}
    assert observed['json'] == {
        'caller_text': 'What is Pipecat?',
        'agent': '09-cascade-d',
        'timeout_seconds': 60,
    }
    assert [turn.text for turn in result['transcription_turns']] == [
        'What is Pipecat?',
        'Pipecat is a voice AI framework.',
    ]
    assert (tmp_path / 'conversation-1-caller.wav').read_bytes() == caller_wav
    assert (tmp_path / 'conversation-1-target.wav').read_bytes() == target_wav
    assert result['recording_handle'].transport == 'pipecat_daily_webrtc'
    assert 'dailyRoom' not in str(result)
    assert 'dailyToken' not in str(result)
