"""Build CAE-compatible vCon evidence from Pipecat execution audio capture.

Reuses ``benchmark_service._vcon_export`` so dialog turns, parties, recording
attachments, and analysis records stay aligned with saved-run / product export.
"""

from __future__ import annotations

from typing import Any

from app.services.benchmark_service import _vcon_export
from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn


def build_execution_vcon(
    *,
    conversation_id: str,
    execution_run_id: str,
    suite_id: str,
    scenario_id: str,
    transport: str,
    transcription_turns: list[TranscriptionTurn] | list[dict[str, Any]],
    recording: AudioRecordingHandle | dict[str, Any] | None,
    termination_reason: str | None = None,
    tester_provenance: dict[str, Any] | None = None,
    extra_analysis_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dialog_source = [_turn_as_dialog(turn) for turn in transcription_turns]
    dialog_source = [item for item in dialog_source if item.get('text')]
    transcript = _transcript_from_dialog(dialog_source)

    call_payload: dict[str, Any] = {}
    if recording is not None:
        if isinstance(recording, AudioRecordingHandle):
            call_payload = recording.as_call_media()
        elif isinstance(recording, dict):
            call_payload = {
                'recording_url': recording.get('uri') or recording.get('recording_url'),
                'recording_sha256': recording.get('sha256') or recording.get('recording_sha256'),
                'mime_type': recording.get('mime_type') or 'audio/wav',
                'duration_ms': recording.get('duration_ms'),
                'transport': recording.get('transport') or transport,
            }

    analysis = {
        'type': 'execution_audio_capture',
        'encoding': 'json',
        'body': {
            'conversation_id': conversation_id,
            'execution_run_id': execution_run_id,
            'suite_id': suite_id,
            'scenario_id': scenario_id,
            'transport': transport,
            'termination_reason': termination_reason,
            'tester_provenance': dict(tester_provenance or {}),
            'dialog_turns': len(dialog_source),
            'recording_captured': bool(call_payload.get('recording_url')),
            **(extra_analysis_body or {}),
        },
    }

    payload: dict[str, Any] = {
        'conversation': {'dialog': dialog_source},
        'transcript': transcript,
    }
    if call_payload.get('recording_url'):
        payload['call'] = call_payload

    exported = _vcon_export(payload, transcript, analysis)
    # Preserve an explicit execution source label for operator UX.
    if exported.get('source_format') in {'conversation', 'call', 'transcript'}:
        exported['source_format'] = 'pipecat_execution'
    return exported


def vcon_summary(vcon_export: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(vcon_export, dict):
        return {
            'available': False,
            'dialog_turns': 0,
            'analysis_count': 0,
            'source_format': None,
            'recording_attached': False,
        }
    dialog = vcon_export.get('dialog')
    analysis = vcon_export.get('analysis')
    attachments = vcon_export.get('attachments')
    recording_attached = False
    if isinstance(attachments, list):
        recording_attached = any(
            isinstance(item, dict) and item.get('type') == 'recording' for item in attachments
        )
    return {
        'available': True,
        'dialog_turns': len(dialog) if isinstance(dialog, list) else 0,
        'analysis_count': len(analysis) if isinstance(analysis, list) else 0,
        'source_format': vcon_export.get('source_format'),
        'appended_analysis_type': vcon_export.get('appended_analysis_type'),
        'recording_attached': recording_attached,
    }


def _turn_as_dialog(turn: TranscriptionTurn | dict[str, Any]) -> dict[str, Any]:
    if isinstance(turn, TranscriptionTurn):
        return turn.as_dialog_item()
    speaker = str(turn.get('speaker') or turn.get('originator') or 'speaker')
    text = str(turn.get('text') or turn.get('body') or '').strip()
    item: dict[str, Any] = {'speaker': speaker, 'text': text, 'role': speaker.lower()}
    if turn.get('act_id'):
        item['act_id'] = turn.get('act_id')
    if isinstance(turn.get('event_types'), list):
        item['event_types'] = list(turn['event_types'])
    return item


def _transcript_from_dialog(dialog: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in dialog:
        speaker = str(item.get('speaker') or 'speaker')
        text = str(item.get('text') or '').strip()
        if text:
            lines.append(f'{speaker}: {text}')
    return '\n'.join(lines)
