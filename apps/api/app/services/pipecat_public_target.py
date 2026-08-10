from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import httpx

from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn
from app.services.reference_generalist_agent import ReferenceRuntimeConfig


DEFAULT_PUBLIC_AGENT = '09-cascade-d'


def run_public_pipecat_call(
    *,
    caller_text: str,
    artifact_dir: Path,
    conversation_id: str,
    timeout_seconds: int,
    public_agent: str = DEFAULT_PUBLIC_AGENT,
    config: ReferenceRuntimeConfig | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    runtime = config or ReferenceRuntimeConfig()
    if not runtime.internal_token:
        raise RuntimeError(
            'Public Pipecat execution requires REFERENCE_AGENT_INTERNAL_TOKEN shared by the API and Pipecat service.'
        )
    request_client = client or httpx.Client(timeout=timeout_seconds + 30)
    try:
        response = request_client.post(
            f'{runtime.pipecat_service_url}/public-pipecat/run',
            headers={'x-cae-reference-token': runtime.internal_token},
            json={
                'caller_text': caller_text,
                'agent': public_agent,
                'timeout_seconds': timeout_seconds,
            },
        )
    finally:
        if client is None:
            request_client.close()
    response.raise_for_status()
    payload = response.json()
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
