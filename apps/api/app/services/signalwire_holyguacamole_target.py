from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn
from app.services.reference_generalist_agent import ReferenceMediaServices, ReferenceRuntimeConfig


DEFAULT_SIGNALWIRE_TARGET_URL = 'https://holyguacamole.signalwire.me/'
SIGNALWIRE_PUBLIC_GATE_ENV = 'CAE_ENABLE_SIGNALWIRE_HOLYGUACAMOLE'
SIGNALWIRE_CALLER_TTS_ATTEMPTS = 2
SIGNALWIRE_CALLER_TTS_ATTEMPT_TIMEOUT_SECONDS = 30.0
REMOTE_SPEECH_TRANSCRIPT_SOURCES = {
    'remote_audio_asr',
    'remote_audio_transcription',
    'signalwire_remote_audio_asr',
    'rtc-asr.current_run',
}


def _open_live_audio_broadcast(
    runtime: ReferenceRuntimeConfig,
    *,
    execution_run_id: str | None,
    conversation_id: str,
) -> str | None:
    """Pre-register the existing CAE listener bus before TTS and WebRTC setup."""
    if not execution_run_id or not runtime.internal_token:
        return None
    try:
        response = httpx.post(
            f'{runtime.pipecat_service_url}/outbound-voice/broadcast/open',
            headers={'x-cae-reference-token': runtime.internal_token},
            json={
                'execution_run_id': execution_run_id,
                'session_id': conversation_id,
            },
            timeout=min(10.0, runtime.timeout_seconds),
        )
        response.raise_for_status()
        publisher_id = str(response.json().get('publisher_id') or '').strip()
        return publisher_id or None
    except Exception:  # noqa: BLE001 - the transport retains late-registration fallback
        return None


def _close_live_audio_broadcast(
    runtime: ReferenceRuntimeConfig,
    *,
    execution_run_id: str | None,
    publisher_id: str | None,
) -> None:
    if not execution_run_id or not publisher_id or not runtime.internal_token:
        return
    try:
        httpx.post(
            f'{runtime.pipecat_service_url}/outbound-voice/broadcast/close',
            headers={'x-cae-reference-token': runtime.internal_token},
            json={
                'execution_run_id': execution_run_id,
                'publisher_id': publisher_id,
            },
            timeout=min(10.0, runtime.timeout_seconds),
        )
    except Exception:  # noqa: BLE001 - Pipecat retires inactive listener buses
        pass


def _transcribe_captured_response(
    result: dict[str, Any],
    *,
    repo_root: Path,
    media: ReferenceMediaServices | None = None,
) -> None:
    transcript = result.get('transcript') if isinstance(result.get('transcript'), dict) else {}
    existing_text = str(transcript.get('agent_text') or '').strip()
    existing_source = str(transcript.get('source') or '').strip()
    if existing_text and existing_source in REMOTE_SPEECH_TRANSCRIPT_SOURCES:
        return

    artifacts = result.get('artifacts') if isinstance(result.get('artifacts'), dict) else {}
    response_audio_value = str(artifacts.get('target_response_audio') or '').strip()
    if not response_audio_value:
        return
    response_audio_path = repo_root / response_audio_value
    if not response_audio_path.exists():
        transcript['asr_error'] = 'Captured target response WAV artifact is missing.'
        result['transcript'] = transcript
        return

    media_services = media
    if media_services is None:
        config = ReferenceRuntimeConfig()
        if config.rtc_asr_base_url:
            media_services = ReferenceMediaServices(config)
    if media_services is None or not media_services.config.rtc_asr_base_url:
        transcript['asr_error'] = 'RTC_ASR_BASE_URL is not configured.'
        result['transcript'] = transcript
        return
    try:
        agent_text = media_services.transcribe(response_audio_path.read_bytes())
    except Exception as exc:  # noqa: BLE001 - preserve captured audio when optional ASR fails
        transcript['asr_error'] = f'rtc-asr transcription failed: {exc}'[:400]
        result['transcript'] = transcript
        return

    caller_text = str(transcript.get('caller_text') or '').strip()
    transcript.update({
        'agent_text': agent_text,
        'agent_text_available': True,
        'complete_as_observed': True,
        'untranscribed_target_audio': False,
        'source': 'rtc-asr.current_run',
        'text': '\n'.join(
            part for part in (
                f'Caller: {caller_text}' if caller_text else '',
                f'Agent: {agent_text}',
            ) if part
        ),
    })
    result['transcript'] = transcript


def run_signalwire_holyguacamole_call(
    *,
    caller_text: str,
    artifact_dir: Path,
    conversation_id: str,
    execution_run_id: str | None = None,
    timeout_seconds: int,
    scenario: dict[str, Any] | None = None,
    max_exchanges: int = 1,
    tester_model_name: str | None = None,
    target_url: str = DEFAULT_SIGNALWIRE_TARGET_URL,
) -> dict[str, Any]:
    if os.getenv(SIGNALWIRE_PUBLIC_GATE_ENV, '').strip().lower() not in {'1', 'true', 'yes'}:
        raise RuntimeError(
            f'Holy Guacamole SignalWire execution requires {SIGNALWIRE_PUBLIC_GATE_ENV}=1.'
        )

    repo_root = Path(__file__).resolve().parents[4]
    artifact_dir.mkdir(parents=True, exist_ok=True)
    runtime = ReferenceRuntimeConfig()
    if not runtime.kokoro_base_url:
        raise RuntimeError(
            'Holy Guacamole SignalWire execution uses CAE tester media and requires '
            'KOKORO_BASE_URL.'
        )
    live_publisher_id = _open_live_audio_broadcast(
        runtime,
        execution_run_id=execution_run_id,
        conversation_id=conversation_id,
    )
    media_runtime = replace(
        runtime,
        timeout_seconds=min(
            runtime.timeout_seconds,
            SIGNALWIRE_CALLER_TTS_ATTEMPT_TIMEOUT_SECONDS,
        ),
    )
    media_services = ReferenceMediaServices(media_runtime)
    synthesis_errors: list[Exception] = []
    caller_audio = b''
    for attempt in range(1, SIGNALWIRE_CALLER_TTS_ATTEMPTS + 1):
        try:
            caller_audio = media_services.synthesize(
                caller_text,
                voice=runtime.kokoro_tester_voice,
            )
            break
        except Exception as exc:  # noqa: BLE001 - normalize the existing media boundary
            synthesis_errors.append(exc)
            status_code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            if status_code is not None and status_code < 500:
                break
            if attempt < SIGNALWIRE_CALLER_TTS_ATTEMPTS:
                media_services.client.close()
                media_services = ReferenceMediaServices(media_runtime)
    if not caller_audio:
        _close_live_audio_broadcast(
            runtime,
            execution_run_id=execution_run_id,
            publisher_id=live_publisher_id,
        )
        last_error = synthesis_errors[-1] if synthesis_errors else None
        error_detail = (
            f' {last_error.__class__.__name__}: {str(last_error).strip()}'
            if last_error is not None
            else ''
        )
        raise RuntimeError(
            'Holy Guacamole SignalWire tester audio synthesis failed through CAE Kokoro '
            f'after {len(synthesis_errors)} attempts.{error_detail}'
        ) from last_error
    caller_audio_dir = artifact_dir / 'tester-media'
    caller_audio_dir.mkdir(parents=True, exist_ok=True)
    caller_audio_path = caller_audio_dir / f'{conversation_id}-caller.wav'
    caller_audio_path.write_bytes(caller_audio)
    caller_audio_sha256 = hashlib.sha256(caller_audio).hexdigest()
    script_path = repo_root / 'scripts' / 'signalwire_holyguacamole_smoke.mjs'
    artifact_root = artifact_dir / 'signalwire-webrtc'
    command = [
        'node',
        str(script_path),
        '--target-url',
        target_url,
        '--caller-text',
        caller_text,
        '--caller-audio',
        str(caller_audio_path),
        '--artifact-root',
        str(artifact_root),
        '--timeout-ms',
        str(max(30, timeout_seconds) * 1000),
        '--max-exchanges',
        str(max_exchanges),
        '--json-only',
    ]
    env = {
        **os.environ,
        'SIGNALWIRE_HOLYGUACAMOLE_ALLOW_PUBLIC': '1',
        'SIGNALWIRE_HOLYGUACAMOLE_CONVERSATION_ID': conversation_id,
        'SIGNALWIRE_HOLYGUACAMOLE_MAX_EXCHANGES': str(max_exchanges),
        'SIGNALWIRE_HOLYGUACAMOLE_SCENARIO_JSON': json.dumps(scenario or {}),
    }
    if tester_model_name:
        env['SIGNALWIRE_HOLYGUACAMOLE_TESTER_MODEL_NAME'] = tester_model_name
    if execution_run_id:
        env['SIGNALWIRE_HOLYGUACAMOLE_EXECUTION_RUN_ID'] = execution_run_id
    if live_publisher_id:
        env['SIGNALWIRE_HOLYGUACAMOLE_LIVE_PUBLISHER_ID'] = live_publisher_id
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(60, timeout_seconds + 45),
        )
    finally:
        _close_live_audio_broadcast(
            runtime,
            execution_run_id=execution_run_id,
            publisher_id=live_publisher_id,
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

    transcript_payload = result.get('transcript') if isinstance(result.get('transcript'), dict) else {}
    transcript_payload.update({
        'caller_text': caller_text,
        'caller_text_verified': True,
        'caller_text_source': 'cae_kokoro_tts',
    })
    result['transcript'] = transcript_payload
    tester_payload = result.get('tester') if isinstance(result.get('tester'), dict) else {}
    tester_payload.update({
        'media_source': 'cae_kokoro_tts',
        'caller_text_verified': True,
        'caller_audio_sha256': caller_audio_sha256,
        'caller_voice': runtime.kokoro_tester_voice,
    })
    result['tester'] = tester_payload
    transcript_turns: list[TranscriptionTurn] = []
    exchange_payloads = result.get('exchanges') if isinstance(result.get('exchanges'), list) else []
    response_turn_artifacts = (
        (result.get('artifacts') or {}).get('target_response_audio_turns')
        if isinstance((result.get('artifacts') or {}).get('target_response_audio_turns'), list)
        else []
    )
    caller_turn_paths: list[Path] = []
    response_turn_paths: list[Path] = []
    if exchange_payloads:
        for exchange_index, exchange in enumerate(exchange_payloads, start=1):
            if not isinstance(exchange, dict):
                continue
            exchange_caller_text = str(exchange.get('caller_text') or '').strip()
            if not exchange_caller_text:
                continue
            if exchange_index == 1:
                exchange_caller_path = caller_audio_path
            else:
                try:
                    exchange_caller_audio = base64.b64decode(
                        str(exchange.get('caller_audio_wav_base64') or ''),
                        validate=True,
                    )
                except ValueError:
                    exchange_caller_audio = b''
                exchange_caller_path = caller_audio_dir / (
                    f'{conversation_id}-caller-turn-{exchange_index}.wav'
                )
                if exchange_caller_audio:
                    exchange_caller_path.write_bytes(exchange_caller_audio)
            caller_turn_paths.append(exchange_caller_path)
            transcript_turns.append(TranscriptionTurn(
                turn_index=len(transcript_turns) + 1,
                speaker='Caller',
                text=exchange_caller_text,
                source='signalwire_webrtc',
                event_types=['webrtc_synthetic_audio_sent'],
                direction='tester_to_target',
                evidence_role='tester',
                frame_metadata={
                    'target_url': DEFAULT_SIGNALWIRE_TARGET_URL,
                    'caller_text_source': (
                        'cae_kokoro_tts'
                        if exchange_index == 1
                        else 'pipecat_reference_tester_turn'
                    ),
                    'exchange': exchange_index,
                    'caller_audio_uri': str(exchange_caller_path),
                },
            ))

            response_entry = (
                response_turn_artifacts[exchange_index - 1]
                if exchange_index <= len(response_turn_artifacts)
                and isinstance(response_turn_artifacts[exchange_index - 1], dict)
                else {}
            )
            response_path_value = str(response_entry.get('path') or '').strip()
            response_path = repo_root / response_path_value if response_path_value else None
            if response_path is not None and response_path.exists():
                response_turn_paths.append(response_path)
            agent_text = str(exchange.get('agent_text') or '').strip()
            if not agent_text and response_path is not None and response_path.exists():
                try:
                    agent_text = media_services.transcribe(response_path.read_bytes())
                except Exception as exc:  # noqa: BLE001
                    transcript_payload['asr_error'] = (
                        f'rtc-asr transcription failed for exchange {exchange_index}: {exc}'
                    )[:400]
            if agent_text:
                exchange['agent_text'] = agent_text
                exchange['agent_text_source'] = 'rtc-asr.current_run'
                transcript_turns.append(TranscriptionTurn(
                    turn_index=len(transcript_turns) + 1,
                    speaker='Agent',
                    text=agent_text,
                    source='signalwire_webrtc',
                    event_types=['remote_audio_captured', 'remote_speech_transcribed'],
                    direction='target_to_tester',
                    evidence_role='target',
                    frame_metadata={
                        'transcript_source': 'rtc-asr.current_run',
                        'exchange': exchange_index,
                        **({'response_audio_uri': str(response_path)} if response_path else {}),
                    },
                ))
    else:
        _transcribe_captured_response(result, repo_root=repo_root, media=media_services)
        transcript_payload = (
            result.get('transcript') if isinstance(result.get('transcript'), dict) else {}
        )
        caller_text_verified = transcript_payload.get('caller_text_verified') is True
        if caller_text_verified:
            transcript_turns.append(TranscriptionTurn(
                turn_index=1,
                speaker='Caller',
                text=str(transcript_payload.get('caller_text') or caller_text),
                source='signalwire_webrtc',
                event_types=['webrtc_synthetic_audio_sent'],
                direction='tester_to_target',
                evidence_role='tester',
                frame_metadata={
                    'target_url': DEFAULT_SIGNALWIRE_TARGET_URL,
                    'caller_text_source': 'cae_kokoro_tts',
                    'exchange': 1,
                    'caller_audio_uri': str(caller_audio_path),
                },
            ))
        agent_text = str(transcript_payload.get('agent_text') or '').strip()
        transcript_source = str(transcript_payload.get('source') or '').strip()
        if agent_text and transcript_source in REMOTE_SPEECH_TRANSCRIPT_SOURCES:
            transcript_turns.append(TranscriptionTurn(
                turn_index=2,
                speaker='Agent',
                text=agent_text,
                source='signalwire_webrtc',
                event_types=['remote_audio_captured', 'remote_speech_transcribed'],
                direction='target_to_tester',
                evidence_role='target',
                frame_metadata={
                    'transcript_source': transcript_source,
                    'exchange': 1,
                },
            ))

    transcript_payload.update({
        'text': '\n'.join(f'{turn.speaker}: {turn.text}' for turn in transcript_turns),
        'agent_text': next(
            (turn.text for turn in reversed(transcript_turns) if turn.speaker == 'Agent'),
            '',
        ),
        'agent_text_available': any(turn.speaker == 'Agent' for turn in transcript_turns),
        'complete_as_observed': len(transcript_turns) == max_exchanges * 2,
        'untranscribed_target_audio': len(transcript_turns) != max_exchanges * 2,
        'source': 'rtc-asr.current_run',
    })
    result['transcript'] = transcript_payload
    result_path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')

    audio_bytes = target_audio_path.read_bytes()
    recording = AudioRecordingHandle(
        uri=str(target_audio_path),
        mime_type=str((result.get('artifacts') or {}).get('target_audio_mime') or 'audio/webm'),
        sha256=str((result.get('artifacts') or {}).get('target_audio_sha256') or ''),
        duration_ms=(result.get('media') or {}).get('target_audio_duration_ms'),
        bytes_captured=len(audio_bytes),
        transport='signalwire_webrtc',
        metadata={
            'transport': 'signalwire_webrtc',
            'scope': 'remote_target_webrtc_capture',
            'caller_audio_uri': str(repo_root / str((result.get('artifacts') or {}).get('caller_audio') or '')),
            'cae_caller_audio_uri': str(caller_audio_path),
            'cae_caller_audio_sha256': caller_audio_sha256,
            'current_run': True,
            'result_json': str(result_path),
            **({
                'response_audio_uri': str(
                    repo_root / str((result.get('artifacts') or {}).get('target_response_audio'))
                ),
            } if (result.get('artifacts') or {}).get('target_response_audio') else {}),
            'caller_audio_turn_uris': [str(path) for path in caller_turn_paths],
            'response_audio_turn_uris': [str(path) for path in response_turn_paths],
        },
    )
    return {
        **result,
        'transcription_turns': transcript_turns,
        'recording_handle': recording,
    }
