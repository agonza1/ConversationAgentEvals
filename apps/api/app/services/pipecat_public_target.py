from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn
from app.services.reference_generalist_agent import ReferenceRuntimeConfig


DEFAULT_PUBLIC_AGENT = '10-gradium'
# The Pipecat route starts its response deadline only after TTS synthesis, public-room
# creation, Daily connect/join, RTVI readiness, and real-time caller playback. Keep the
# HTTP client alive for those bounded setup stages plus the longest accepted caller text.
PUBLIC_PIPECAT_SETUP_AND_PLAYBACK_ALLOWANCE_SECONDS = 360


def run_public_pipecat_call(
    *,
    caller_text: str,
    artifact_dir: Path,
    conversation_id: str,
    execution_run_id: str | None = None,
    timeout_seconds: int,
    scenario: dict[str, Any] | None = None,
    max_exchanges: int = 1,
    tester_model_name: str | None = None,
    event_observer: Any | None = None,
    public_agent: str = DEFAULT_PUBLIC_AGENT,
    config: ReferenceRuntimeConfig | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    runtime = config or ReferenceRuntimeConfig()
    if not runtime.internal_token:
        raise RuntimeError(
            'Public Pipecat execution requires REFERENCE_AGENT_INTERNAL_TOKEN shared by the API and Pipecat service.'
        )
    request_client = client or httpx.Client(timeout=(
        timeout_seconds * max_exchanges + PUBLIC_PIPECAT_SETUP_AND_PLAYBACK_ALLOWANCE_SECONDS
    ))
    try:
        request_payload = {
            'caller_text': caller_text,
            'agent': public_agent,
            'timeout_seconds': timeout_seconds,
            'scenario': scenario or {'id': 'public-pipecat', 'goal': caller_text},
            'max_turn_pairs': max_exchanges,
            'tester_model_name': tester_model_name,
            'execution_run_id': execution_run_id,
            'session_id': conversation_id,
        }
        with request_client.stream(
            'POST',
            f'{runtime.pipecat_service_url}/public-pipecat/duplex',
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
                if detail.startswith('Public Pipecat'):
                    raise RuntimeError(detail) from exc
                raise
            payload: dict[str, Any] | None = None
            for line in response.iter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError('Public Pipecat duplex stream returned invalid NDJSON.') from exc
                event_type = str(event.get('type') or '')
                if event_type == 'error':
                    raise RuntimeError(str(event.get('detail') or 'Public Pipecat direct call failed.'))
                if event_type == 'complete':
                    payload = event.get('result') if isinstance(event.get('result'), dict) else None
                    continue
                if event_type == 'phase' and event_observer is not None:
                    event_observer({
                        'speaker': 'Connection',
                        'text': str(event.get('text') or ''),
                        'frame_metadata': {
                            'transport': 'pipecat_daily_webrtc',
                            'current_run': True,
                            'turn_pair': int(event.get('turn_pair') or 0),
                            'media_event': 'connection_phase',
                            'connection_phase': str(event.get('phase') or ''),
                        },
                    })
                    continue
                if event_observer is None or event_type not in {'live_audio', 'live_transcript'}:
                    continue
                turn_pair = int(event.get('turn_pair') or 0)
                direction = str(event.get('direction') or '')
                live_key = f'{turn_pair}:{direction}'
                observed_event = {
                    'speaker': str(event.get('speaker') or ''),
                    'text': str(event.get('text') or ''),
                    'direction': direction,
                    'frame_metadata': {
                        'transport': 'pipecat_daily_webrtc',
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
                raise RuntimeError('Public Pipecat duplex stream ended without completion evidence.')
    finally:
        if client is None:
            request_client.close()
    if payload.get('status') != 'pass':
        raise RuntimeError(str(payload.get('reason') or 'Public Pipecat direct call did not pass.'))

    raw_turns = payload.get('turns') if isinstance(payload.get('turns'), list) else []
    turns: list[TranscriptionTurn] = []
    for index, item in enumerate(raw_turns, start=1):
        if not isinstance(item, dict):
            continue
        speaker = str(item.get('speaker') or '').strip().lower()
        text = str(item.get('text') or '').strip()
        if speaker not in {'caller', 'agent'} or not text:
            continue
        turns.append(TranscriptionTurn(
            turn_index=index,
            speaker=speaker.title(),
            text=text,
            source='pipecat_public_daily',
            event_types=[
                'daily_audio_sent' if speaker == 'caller' else 'daily_audio_received',
                'rtvi_transcript_observed',
            ],
            direction='tester_to_target' if speaker == 'caller' else 'target_to_tester',
            evidence_role='tester' if speaker == 'caller' else 'target',
        ))
    if len(turns) < 2 or turns[0].speaker != 'Caller' or turns[-1].speaker != 'Agent':
        raise RuntimeError('Public Pipecat direct call returned incomplete caller/agent transcript evidence.')

    media = payload.get('media') if isinstance(payload.get('media'), dict) else {}
    caller_wav = _decode_audio(media.get('caller_audio_wav_base64'), label='caller')
    target_wav = _decode_audio(media.get('target_audio_wav_base64'), label='target')
    artifact_dir.mkdir(parents=True, exist_ok=True)
    caller_path = artifact_dir / f'{conversation_id}-caller.wav'
    target_path = artifact_dir / f'{conversation_id}-target.wav'
    caller_path.write_bytes(caller_wav)
    target_path.write_bytes(target_wav)
    target_sha = hashlib.sha256(target_wav).hexdigest()
    recording = AudioRecordingHandle(
        uri=str(target_path),
        mime_type='audio/wav',
        sha256=target_sha,
        bytes_captured=len(target_wav),
        transport='pipecat_daily_webrtc',
        metadata={
            'transport': 'pipecat_daily_webrtc',
            'scope': 'target_response_only',
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


def _decode_audio(value: Any, *, label: str) -> bytes:
    try:
        payload = base64.b64decode(str(value or ''), validate=True)
    except ValueError as exc:
        raise RuntimeError(f'Public Pipecat direct call returned invalid {label} audio.') from exc
    if not payload:
        raise RuntimeError(f'Public Pipecat direct call returned no {label} audio.')
    return payload
