from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx

from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn
from app.services.reference_generalist_agent import ReferenceRuntimeConfig


DEFAULT_SIGNALWIRE_TARGET_URL = 'https://holyguacamole.signalwire.me/'
SIGNALWIRE_PUBLIC_GATE_ENV = 'CAE_ENABLE_SIGNALWIRE_HOLYGUACAMOLE'
SIGNALWIRE_DIRECT_SETUP_AND_PLAYBACK_ALLOWANCE_SECONDS = 360
REMOTE_SPEECH_TRANSCRIPT_SOURCES = {
    'remote_audio_asr',
    'remote_audio_transcription',
    'signalwire_remote_audio_asr',
    'rtc-asr.current_run',
}


def run_signalwire_holyguacamole_call(
    *,
    caller_text: str,
    artifact_dir: Path,
    conversation_id: str,
    execution_run_id: str | None = None,
    timeout_seconds: int,
    scenario: dict[str, Any] | None = None,
    target_url: str = DEFAULT_SIGNALWIRE_TARGET_URL,
    max_exchanges: int = 1,
    tester_model_name: str | None = None,
    event_observer: Any | None = None,
    config: ReferenceRuntimeConfig | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if os.getenv(SIGNALWIRE_PUBLIC_GATE_ENV, '').strip().lower() not in {'1', 'true', 'yes'}:
        raise RuntimeError(
            f'Holy Guacamole SignalWire execution requires {SIGNALWIRE_PUBLIC_GATE_ENV}=1.'
        )
    runtime = config or ReferenceRuntimeConfig()
    if not runtime.internal_token:
        raise RuntimeError(
            'Holy Guacamole SignalWire execution requires REFERENCE_AGENT_INTERNAL_TOKEN shared by the API and Pipecat service.'
        )
    request_client = client or httpx.Client(timeout=(
        timeout_seconds + SIGNALWIRE_DIRECT_SETUP_AND_PLAYBACK_ALLOWANCE_SECONDS
    ))
    try:
        request_payload = {
            'caller_text': caller_text,
            'timeout_seconds': timeout_seconds,
            'scenario': scenario or {'id': 'signalwire-holyguacamole', 'goal': caller_text},
            'max_turn_pairs': max_exchanges,
            'tester_model_name': tester_model_name,
            'execution_run_id': execution_run_id,
            'session_id': conversation_id,
            'target_url': target_url,
        }
        with request_client.stream(
            'POST',
            f'{runtime.pipecat_service_url}/signalwire-holyguacamole/duplex',
            headers={'x-cae-reference-token': runtime.internal_token},
            json=request_payload,
        ) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = ''
                try:
                    error_payload = json.loads(response.read())
                    if isinstance(error_payload, dict):
                        detail = str(error_payload.get('detail') or '').strip()
                except (TypeError, ValueError):
                    pass
                if detail.startswith('Holy Guacamole'):
                    raise RuntimeError(detail) from exc
                raise
            payload: dict[str, Any] | None = None
            for line in response.iter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError('Holy Guacamole SignalWire stream returned invalid NDJSON.') from exc
                event_type = str(event.get('type') or '')
                if event_type == 'error':
                    raise RuntimeError(str(event.get('detail') or 'Holy Guacamole SignalWire direct call failed.'))
                if event_type == 'complete':
                    payload = event.get('result') if isinstance(event.get('result'), dict) else None
                    continue
                if event_observer is None or event_type not in {'live_audio', 'live_transcript', 'phase'}:
                    continue
                turn_pair = int(event.get('turn_pair') or 0)
                direction = str(event.get('direction') or '')
                live_key = f'{turn_pair}:{direction}'
                observed_event = {
                    'speaker': str(event.get('speaker') or 'Connection'),
                    'text': str(event.get('text') or ''),
                    'direction': direction or None,
                    'frame_metadata': {
                        'transport': 'signalwire_direct_webrtc',
                        'current_run': True,
                        'turn_pair': turn_pair,
                        'media_event': str(event.get('media_event') or event_type),
                        'listener_media_key': str(event.get('listener_media_key') or ''),
                    },
                    'update_live_audio_key': live_key,
                    'live_audio_key': live_key,
                }
                if event_type == 'live_audio':
                    observed_event['audio'] = _decode_audio(event.get('audio_wav_base64'), label='live')
                event_observer(observed_event)
            if payload is None:
                raise RuntimeError('Holy Guacamole SignalWire stream ended without completion evidence.')
    finally:
        if client is None:
            request_client.close()
    if payload.get('status') != 'pass':
        raise RuntimeError(str(payload.get('reason') or 'Holy Guacamole SignalWire direct call did not pass.'))

    turns = _transcription_turns(payload, caller_text)
    media = payload.get('media') if isinstance(payload.get('media'), dict) else {}
    caller_wav = _decode_audio(media.get('caller_audio_wav_base64'), label='caller')
    target_wav = _decode_audio(media.get('target_audio_wav_base64'), label='target')
    artifact_dir.mkdir(parents=True, exist_ok=True)
    caller_path = artifact_dir / f'{conversation_id}-signalwire-caller.wav'
    target_path = artifact_dir / f'{conversation_id}-signalwire-target.wav'
    caller_path.write_bytes(caller_wav)
    target_path.write_bytes(target_wav)
    recording = AudioRecordingHandle(
        uri=str(target_path),
        mime_type='audio/wav',
        sha256=hashlib.sha256(target_wav).hexdigest(),
        bytes_captured=len(target_wav),
        transport='signalwire_direct_webrtc',
        metadata={
            'transport': 'signalwire_direct_webrtc',
            'scope': 'remote_target_direct_capture',
            'caller_audio_uri': str(caller_path),
            'caller_audio_sha256': hashlib.sha256(caller_wav).hexdigest(),
            'current_run': True,
        },
    )
    return {
        **payload,
        'transcription_turns': turns,
        'recording_handle': recording,
        'caller_audio_path': str(caller_path),
        'target_audio_path': str(target_path),
    }


def _transcription_turns(payload: dict[str, Any], caller_text: str) -> list[TranscriptionTurn]:
    transcript_payload = payload.get('transcript') if isinstance(payload.get('transcript'), dict) else {}
    turns: list[TranscriptionTurn] = []
    if transcript_payload.get('caller_text_verified') is True:
        turns.append(TranscriptionTurn(
            turn_index=1,
            speaker='Caller',
            text=str(transcript_payload.get('caller_text') or caller_text),
            source='signalwire_direct_webrtc',
            event_types=['signalwire_direct_audio_sent'],
            direction='tester_to_target',
            evidence_role='tester',
            frame_metadata={
                'target_url': DEFAULT_SIGNALWIRE_TARGET_URL,
                'caller_text_source': str(
                    transcript_payload.get('caller_text_source') or 'current_run_kokoro'
                ),
            },
        ))
    agent_text = str(transcript_payload.get('agent_text') or '').strip()
    transcript_source = str(transcript_payload.get('source') or '').strip()
    if agent_text and transcript_source in REMOTE_SPEECH_TRANSCRIPT_SOURCES:
        turns.append(TranscriptionTurn(
            turn_index=len(turns) + 1,
            speaker='Agent',
            text=agent_text,
            source='signalwire_direct_webrtc',
            event_types=['remote_audio_captured', 'remote_speech_transcribed'],
            direction='target_to_tester',
            evidence_role='target',
            frame_metadata={'transcript_source': transcript_source},
        ))
    return turns


def _decode_audio(value: Any, *, label: str) -> bytes:
    try:
        payload = base64.b64decode(str(value or ''), validate=True)
    except ValueError as exc:
        raise RuntimeError(f'Holy Guacamole SignalWire direct call returned invalid {label} audio.') from exc
    if not payload:
        raise RuntimeError(f'Holy Guacamole SignalWire direct call returned no {label} audio.')
    return payload
