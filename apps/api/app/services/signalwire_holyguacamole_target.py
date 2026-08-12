from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn


DEFAULT_SIGNALWIRE_TARGET_URL = 'https://holyguacamole.signalwire.me/'
SIGNALWIRE_PUBLIC_GATE_ENV = 'CAE_ENABLE_SIGNALWIRE_HOLYGUACAMOLE'
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
) -> dict[str, Any]:
    if os.getenv(SIGNALWIRE_PUBLIC_GATE_ENV, '').strip().lower() not in {'1', 'true', 'yes'}:
        raise RuntimeError(
            f'Holy Guacamole SignalWire execution requires {SIGNALWIRE_PUBLIC_GATE_ENV}=1.'
        )

    repo_root = Path(__file__).resolve().parents[4]
    artifact_dir.mkdir(parents=True, exist_ok=True)
    script_path = repo_root / 'scripts' / 'signalwire_holyguacamole_smoke.mjs'
    artifact_root = artifact_dir / 'signalwire-browser'
    command = [
        'node',
        str(script_path),
        '--target-url',
        target_url,
        '--caller-text',
        caller_text,
        '--artifact-root',
        str(artifact_root),
        '--timeout-ms',
        str(max(30, timeout_seconds) * 1000),
        '--json-only',
    ]
    env = {
        **os.environ,
        'SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC': '1',
        'SIGNALWIRE_HOLYGUACAMOLE_CONVERSATION_ID': conversation_id,
    }
    if execution_run_id:
        env['SIGNALWIRE_HOLYGUACAMOLE_EXECUTION_RUN_ID'] = execution_run_id
    completed = subprocess.run(
        command,
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(60, timeout_seconds + 45),
    )
    stdout = completed.stdout.strip()
    try:
        summary = json.loads(stdout.splitlines()[-1]) if stdout else {}
    except (json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError('Holy Guacamole SignalWire smoke returned invalid JSON.') from exc
    result_path = repo_root / str(summary.get('result_path') or '')
    if not result_path.exists():
        detail = (completed.stderr or stdout or '').strip()
        raise RuntimeError(
            'Holy Guacamole SignalWire smoke did not produce a result artifact.'
            + (f' Detail: {detail[:400]}' if detail else '')
        )
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if result.get('status') != 'pass':
        reason = str(result.get('reason') or result.get('reason_code') or 'SignalWire run did not pass.')
        raise RuntimeError(f'Holy Guacamole SignalWire run blocked: {reason}')

    target_audio_path = repo_root / str((result.get('artifacts') or {}).get('target_audio') or '')
    if not target_audio_path.exists():
        raise RuntimeError('Holy Guacamole SignalWire run did not capture remote audio.')

    transcript_turns = [
        TranscriptionTurn(
            turn_index=1,
            speaker='Caller',
            text=caller_text,
            source='signalwire_browser_webrtc',
            event_types=['browser_synthetic_audio_sent'],
            direction='tester_to_target',
            evidence_role='tester',
            frame_metadata={'target_url': DEFAULT_SIGNALWIRE_TARGET_URL},
        )
    ]
    transcript_payload = result.get('transcript') if isinstance(result.get('transcript'), dict) else {}
    agent_text = str(transcript_payload.get('agent_text') or '').strip()
    transcript_source = str(transcript_payload.get('source') or '').strip()
    if agent_text and transcript_source in REMOTE_SPEECH_TRANSCRIPT_SOURCES:
        transcript_turns.append(TranscriptionTurn(
            turn_index=2,
            speaker='Agent',
            text=agent_text,
            source='signalwire_browser_webrtc',
            event_types=['remote_audio_captured', 'remote_speech_transcribed'],
            direction='target_to_tester',
            evidence_role='target',
            frame_metadata={'transcript_source': transcript_source},
        ))

    audio_bytes = target_audio_path.read_bytes()
    recording = AudioRecordingHandle(
        uri=str(target_audio_path),
        mime_type=str((result.get('artifacts') or {}).get('target_audio_mime') or 'audio/webm'),
        sha256=str((result.get('artifacts') or {}).get('target_audio_sha256') or ''),
        duration_ms=(result.get('media') or {}).get('target_audio_duration_ms'),
        bytes_captured=len(audio_bytes),
        transport='signalwire_browser_webrtc',
        metadata={
            'transport': 'signalwire_browser_webrtc',
            'scope': 'remote_target_browser_capture',
            'caller_audio_uri': str(repo_root / str((result.get('artifacts') or {}).get('caller_audio') or '')),
            'current_run': True,
            'result_json': str(result_path),
        },
    )
    return {
        **result,
        'transcription_turns': transcript_turns,
        'recording_handle': recording,
    }
