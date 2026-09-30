"""Build CAE-compatible vCon evidence from Pipecat execution audio capture.

Reuses ``benchmark_service._vcon_export`` so dialog turns, parties, recording
attachments, and analysis records stay aligned with saved-run / product export.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.services.benchmark_service import _vcon_export
from app.services.execution_audio import AudioRecordingHandle, TranscriptionTurn
from app.services.vcon_interop import (
    IETF_VCON_CORE_DRAFT,
    IETF_VCON_PRODUCT,
    IETF_VCON_VENDOR,
    IETF_VCON_VERSION,
)


# draft-ietf-vcon-vcon-core-04.  Keep this separate from CAE's long-standing
# ``vcon_export`` object below: that object deliberately contains CAE-specific
# evidence fields and is not a portable IETF vCon document.
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


def build_ietf_execution_vcon(
    *,
    conversation_id: str,
    execution_run_id: str,
    suite_id: str,
    scenario_id: str,
    scenario_title: str | None,
    mode: str,
    turns: list[Any],
    recording: AudioRecordingHandle | dict[str, Any] | None,
    created_at: str | None,
    updated_at: str | None,
    final_state: dict[str, Any] | None = None,
    verdict: str | None = None,
    score: float | None = None,
    action_trace: Any = None,
    live_events: list[Any] | None = None,
    latency_marks: list[Any] | None = None,
    state_snapshots: list[dict[str, Any]] | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    """Build a portable IETF vCon without leaking CAE-only evidence fields.

    The conversation ``dialog`` represents the text passed to TTS (when it is
    available) rather than a downstream ASR receipt.  Receipt text, WER, and
    CAE evaluation details remain useful but belong in analysis evidence.
    """
    captured_at = _normalise_timestamp(created_at)
    changed_at = _normalise_timestamp(updated_at, fallback=captured_at)
    parties = [
        {'name': 'Caller', 'type': 'bot', 'validation': 'none'},
        {'name': 'Agent', 'type': 'bot', 'validation': 'none'},
    ]
    dialog: list[dict[str, Any]] = []
    transcript_turns: list[dict[str, Any]] = []

    for position, raw_turn in enumerate(turns):
        turn = _turn_as_mapping(raw_turn)
        source_text, asr_receipt = _spoken_and_receipt(turn)
        if not source_text:
            continue
        party_index = _party_index(turn)
        dialog_index = len(dialog)
        dialog.append({
            'type': 'text',
            'parties': [party_index],
            'mediatype': 'text/plain',
            'body': source_text,
            'encoding': 'none',
        })
        metadata = turn.get('frame_metadata')
        transcript_turn: dict[str, Any] = {
            'dialog': dialog_index,
            'turn_index': turn.get('turn_index', position + 1),
            'speaker': parties[party_index]['name'].lower(),
            'spoken_text': source_text,
        }
        if asr_receipt:
            transcript_turn['peer_asr_receipt'] = asr_receipt
        if isinstance(metadata, dict) and metadata.get('word_error_rate') is not None:
            transcript_turn['word_error_rate'] = metadata['word_error_rate']
        if turn.get('direction'):
            transcript_turn['direction'] = turn['direction']
        transcript_turns.append(transcript_turn)

    recording_dialog, recording_status = _portable_recording_dialog(recording)
    if recording_dialog:
        dialog.append(recording_dialog)

    dialog_indexes = list(range(len(dialog)))
    analysis: list[dict[str, Any]] = [{
        'type': 'transcript',
        'dialog': dialog_indexes,
        'vendor': IETF_VCON_VENDOR,
        'product': IETF_VCON_PRODUCT,
        'schema': 'cae-execution-transcript-v1',
        'mediatype': 'application/json',
        'encoding': 'json',
        'body': {
            'capture_method': 'tts_source_text_with_peer_asr_receipts',
            'turns': transcript_turns,
        },
    }, {
        'type': 'evaluation',
        'dialog': dialog_indexes,
        'vendor': IETF_VCON_VENDOR,
        'product': IETF_VCON_PRODUCT,
        'schema': 'cae-execution-evidence-v1',
        'mediatype': 'application/json',
        'encoding': 'json',
        'body': {
            'vcon_core_draft': IETF_VCON_CORE_DRAFT,
            'execution_run_id': execution_run_id,
            'conversation_id': conversation_id,
            'suite_id': suite_id,
            'scenario_id': scenario_id,
            'mode': mode,
            'verdict': verdict,
            'score': score,
            'final_state': _portable_final_state(final_state),
            'recording': recording_status,
        },
    }]

    exported: dict[str, Any] = {
        'vcon': IETF_VCON_VERSION,
        'uuid': str(uuid.uuid4()),
        'created_at': captured_at,
        'updated_at': changed_at,
        'subject': scenario_title or scenario_id,
        'parties': parties,
        'dialog': dialog,
        'analysis': analysis,
    }
    from app.services.vcon_evidence import attach_evidence, capture_voice_events, evidence_body
    exported = attach_evidence(exported, evidence_body(
        action_trace=action_trace, final_state=final_state,
        state_snapshots=state_snapshots,
        voice_events=capture_voice_events(turns, live_events or [], latency_marks or []),
        context={'execution_run_id': execution_run_id, 'conversation_id': conversation_id,
                 'suite_id': suite_id, 'scenario_id': scenario_id, 'mode': mode,
                 'completed_at': changed_at}, synthetic=synthetic,
    ))
    validation = validate_ietf_vcon(exported)
    if not validation['valid']:
        raise ValueError(
            f"IETF vCon construction failed validation: {', '.join(validation['errors'])}"
        )
    return exported


def ietf_vcon_summary(vcon_export: dict[str, Any] | None) -> dict[str, Any]:
    validation = validate_ietf_vcon(vcon_export)
    dialog = vcon_export.get('dialog') if isinstance(vcon_export, dict) else []
    recording_available = any(
        isinstance(item, dict) and item.get('type') == 'recording'
        for item in dialog if isinstance(dialog, list)
    )
    return {
        'available': isinstance(vcon_export, dict),
        'valid': validation['valid'],
        'standard_draft': IETF_VCON_CORE_DRAFT,
        'version': vcon_export.get('vcon') if isinstance(vcon_export, dict) else None,
        'dialog_turns': len(dialog) if isinstance(dialog, list) else 0,
        'recording_portable': recording_available,
        'signed': False,
        'errors': validation['errors'],
    }


def validate_ietf_vcon(vcon_export: Any) -> dict[str, Any]:
    """Validate the portable subset CAE emits for draft-ietf-vcon-vcon-core-04.

    This is intentionally a narrow semantic validation rather than pretending
    to be a general-purpose validator for every optional vCon feature.
    """
    errors: list[str] = []
    if not isinstance(vcon_export, dict):
        return {'valid': False, 'errors': ['vCon must be an object']}
    if vcon_export.get('vcon') != IETF_VCON_VERSION:
        errors.append(f"vcon must be {IETF_VCON_VERSION}")
    value_uuid = vcon_export.get('uuid')
    try:
        uuid.UUID(str(value_uuid))
    except (TypeError, ValueError, AttributeError):
        errors.append('uuid must be a UUID')
    for key in ('created_at', 'updated_at'):
        if not _is_timestamp(vcon_export.get(key)):
            errors.append(f'{key} must be an RFC 3339 timestamp')
    parties = vcon_export.get('parties')
    if not isinstance(parties, list) or not parties:
        errors.append('parties must contain at least one participant')
    dialog = vcon_export.get('dialog')
    if not isinstance(dialog, list):
        errors.append('dialog must be a list')
        dialog = []
    for index, item in enumerate(dialog):
        if not isinstance(item, dict):
            errors.append(f'dialog[{index}] must be an object')
            continue
        if item.get('type') == 'text':
            if not isinstance(item.get('body'), str) or not item['body'].strip():
                errors.append(f'dialog[{index}] text body is required')
            if item.get('mediatype') != 'text/plain':
                errors.append(f'dialog[{index}] text mediatype must be text/plain')
        elif item.get('type') == 'recording':
            parsed = urlparse(str(item.get('url') or ''))
            if parsed.scheme != 'https':
                errors.append(f'dialog[{index}] recording URL must use HTTPS')
            if not _is_sha512_base64url(item.get('content_hash')):
                errors.append(
                    f'dialog[{index}] recording content_hash must be a base64url SHA-512 digest'
                )
        else:
            errors.append(f'dialog[{index}] has unsupported type')
    analysis = vcon_export.get('analysis')
    if not isinstance(analysis, list) or not analysis:
        errors.append('analysis must contain CAE provenance')
    return {'valid': not errors, 'errors': errors}


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
    for key in ('source', 'direction', 'evidence_role'):
        value = turn.get(key)
        if value:
            item[key] = value
    if isinstance(turn.get('frame_metadata'), dict) and turn['frame_metadata']:
        item['frame_metadata'] = dict(turn['frame_metadata'])
    return item


def _turn_as_mapping(turn: Any) -> dict[str, Any]:
    if isinstance(turn, TranscriptionTurn):
        return turn.as_dialog_item()
    if isinstance(turn, dict):
        return dict(turn)
    model_dump = getattr(turn, 'model_dump', None)
    if callable(model_dump):
        dumped = model_dump()
        return dict(dumped) if isinstance(dumped, dict) else {}
    return {}


def _spoken_and_receipt(turn: dict[str, Any]) -> tuple[str, str | None]:
    metadata = turn.get('frame_metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    source_text = (
        metadata.get('source_text')
        or turn.get('llm_output')
        or turn.get('text')
        or turn.get('body')
        or ''
    )
    receipt = metadata.get('asr_receipt') or turn.get('asr_receipt')
    normalized_source = str(source_text).strip()
    normalized_receipt = str(receipt).strip() if receipt else None
    return normalized_source, normalized_receipt or None


def _party_index(turn: dict[str, Any]) -> int:
    direction = str(turn.get('direction') or '').lower()
    if direction == 'tester_to_target':
        return 0
    if direction == 'target_to_tester':
        return 1
    speaker = str(turn.get('speaker') or '').lower()
    if speaker in {'caller', 'tester', 'user', 'patient', 'customer'}:
        return 0
    return 1


def _portable_final_state(final_state: dict[str, Any] | None) -> dict[str, Any]:
    """Keep a useful outcome summary without exporting CAE runtime internals."""
    source = final_state if isinstance(final_state, dict) else {}
    allowed_keys = (
        'complete',
        'outcome',
        'termination_reason',
        'tester_termination_reason',
        'evidence_scope',
    )
    return {key: source[key] for key in allowed_keys if key in source}


def _portable_recording_dialog(
    recording: AudioRecordingHandle | dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if recording is None:
        return None, {'status': 'not_captured'}
    media = (
        recording.as_call_media()
        if isinstance(recording, AudioRecordingHandle)
        else dict(recording)
    )
    url = str(media.get('recording_url') or media.get('uri') or '').strip()
    digest = str(media.get('recording_sha512') or media.get('sha512') or '').strip()
    parsed = urlparse(url)
    if parsed.scheme != 'https':
        return None, {
            'status': 'retained_locally',
            'reason': (
                'Portable vCon recording requires an HTTPS URL and is not exported '
                'from local storage.'
            ),
        }
    if not digest:
        return None, {
            'status': 'not_portable',
            'reason': 'Portable vCon recording requires a SHA-512 content hash.',
        }
    if not _is_sha512_base64url(digest):
        return None, {
            'status': 'not_portable',
            'reason': (
                'Portable vCon recording requires a base64url-encoded SHA-512 '
                'content hash.'
            ),
        }
    content_hash = digest
    metadata = media.get('metadata')
    scope = str(metadata.get('scope') or '') if isinstance(metadata, dict) else ''
    parties = [1] if 'target' in scope else [0] if 'caller' in scope else [0, 1]
    item: dict[str, Any] = {
        'type': 'recording',
        'parties': parties,
        'mediatype': str(media.get('mime_type') or 'audio/wav'),
        'url': url,
        'content_hash': content_hash,
    }
    duration_ms = media.get('duration_ms')
    if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
        item['duration'] = duration_ms / 1000
    return item, {'status': 'portable', 'url': url, 'content_hash': content_hash}


def _normalise_timestamp(value: str | None, *, fallback: str | None = None) -> str:
    candidate = value or fallback
    if candidate:
        try:
            parsed = datetime.fromisoformat(str(candidate).replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC).isoformat().replace('+00:00', 'Z')
        except ValueError:
            pass
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _is_sha512_base64url(value: Any) -> bool:
    """Return whether ``value`` is an unpadded/optionally padded SHA-512 digest."""
    if not isinstance(value, str) or not value.strip() or value.startswith('sha512-'):
        return False
    try:
        digest = value.strip()
        padded = digest + ('=' * (-len(digest) % 4))
        decoded = base64.b64decode(
            padded.encode('ascii'), altchars=b'-_', validate=True
        )
    except (UnicodeEncodeError, ValueError, binascii.Error):
        return False
    return len(decoded) == 64


def _transcript_from_dialog(dialog: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in dialog:
        speaker = str(item.get('speaker') or 'speaker')
        text = str(item.get('text') or '').strip()
        if text:
            lines.append(f'{speaker}: {text}')
    return '\n'.join(lines)
