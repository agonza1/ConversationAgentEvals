from __future__ import annotations

import hashlib
import copy
import json
import re
from datetime import UTC, datetime
from typing import Any

from app.schemas.evals import EvalArtifact, EvalAuditEvent, EvalCheck, EvalRunRequest, EvalRunResponse


def _split_criteria(criteria: str) -> list[str]:
    lines = [line.strip(' -\t') for line in criteria.splitlines()]
    cleaned = [line for line in lines if line]
    if len(cleaned) > 1:
        return cleaned[:8]

    parts = re.split(r';|\band\b|,', criteria)
    return [part.strip(' .') for part in parts if part.strip(' .')][:8] or [criteria.strip()]


def _extract_vcon_text(payload: dict[str, Any]) -> str:
    dialog = payload.get('dialog')
    if not isinstance(dialog, list):
        return json.dumps(payload, indent=2, sort_keys=True)

    turns: list[str] = []
    parties = payload.get('parties') if isinstance(payload.get('parties'), list) else []
    for item in dialog:
        if not isinstance(item, dict):
            continue
        body = item.get('body') or item.get('text') or item.get('transcript')
        if not body:
            continue
        party_label = item.get('party') or item.get('speaker') or item.get('originator')
        if isinstance(party_label, int) and 0 <= party_label < len(parties):
            party = parties[party_label]
            if isinstance(party, dict):
                party_label = party.get('name') or party.get('tel') or party_label
        turns.append(f'{party_label or "speaker"}: {body}')
    return '\n'.join(turns) if turns else json.dumps(payload, indent=2, sort_keys=True)


SPEAKER_LABEL_PATTERN = re.compile(r'(?:(?<=^)|(?<=\s))([A-Za-z][A-Za-z0-9 _-]{0,30}):\s*')


def _iter_transcript_turns(transcript: str) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []

    for line in transcript.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue

        matches = list(SPEAKER_LABEL_PATTERN.finditer(cleaned))
        if not matches:
            turns.append(('speaker', cleaned))
            continue

        for index, match in enumerate(matches):
            next_match = matches[index + 1] if index + 1 < len(matches) else None
            body = cleaned[match.end() : next_match.start() if next_match else len(cleaned)].strip()
            if body:
                turns.append((match.group(1).strip() or 'speaker', body))

    return turns


def _transcript_to_dialog(transcript: str) -> list[dict[str, Any]]:
    dialog: list[dict[str, Any]] = []
    party_indexes: dict[str, int] = {}

    for speaker, body in _iter_transcript_turns(transcript):
        normalized_speaker = speaker.lower()
        if normalized_speaker not in party_indexes:
            party_indexes[normalized_speaker] = len(party_indexes)

        dialog.append(
            {
                'party': party_indexes[normalized_speaker],
                'originator': speaker,
                'body': body,
            }
        )

    return dialog


def _build_vcon_export(
    transcript: str,
    source_format: str,
    parsed_source: dict[str, Any] | None,
    analysis: dict[str, Any],
    call: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if parsed_source is not None:
        exported = copy.deepcopy(parsed_source)
    else:
        dialog = _transcript_to_dialog(transcript)
        party_names = []
        seen = set()
        for item in dialog:
            originator = str(item.get('originator') or 'speaker')
            key = originator.lower()
            if key not in seen:
                seen.add(key)
                party_names.append(originator)

        exported = {
            'vcon': '0.0.1',
            'parties': [{'name': name} for name in party_names] or [{'name': 'speaker'}],
            'dialog': dialog or [{'party': 0, 'body': transcript}],
        }

    existing_analysis = exported.get('analysis')
    if isinstance(existing_analysis, list):
        analyses = existing_analysis
    elif existing_analysis:
        analyses = [existing_analysis]
    else:
        analyses = []

    analyses.append(analysis)
    exported['analysis'] = analyses
    exported['appended_analysis_type'] = analysis.get('type')
    exported['source_format'] = source_format
    attachments = _vcon_call_attachments(call)
    if attachments:
        existing_attachments = exported.get('attachments')
        if isinstance(existing_attachments, list):
            exported['attachments'] = [*existing_attachments, *attachments]
        elif existing_attachments:
            exported['attachments'] = [existing_attachments, *attachments]
        else:
            exported['attachments'] = attachments
    return exported


def normalize_conversation(raw: str | dict[str, Any] | list[Any]) -> tuple[str, str, dict[str, Any] | None]:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip(), 'transcript', None
    else:
        parsed = raw

    if isinstance(parsed, dict) and ('dialog' in parsed or 'parties' in parsed):
        return _extract_vcon_text(parsed), 'vcon', parsed

    if isinstance(parsed, dict):
        for key in ('transcript', 'conversation', 'text', 'call_log'):
            value = parsed.get(key)
            if isinstance(value, str):
                return value.strip(), 'json', parsed
        return json.dumps(parsed, indent=2, sort_keys=True), 'json', parsed

    return json.dumps(parsed, indent=2, sort_keys=True), 'json', None


def _evidence_for(transcript: str, criterion: str) -> list[str]:
    words = [word.lower() for word in re.findall(r'[a-zA-Z]{4,}', criterion) if word.lower() not in {'agent', 'caller', 'customer', 'should', 'must', 'call'}]
    if any(word in {'greet', 'greets', 'greeting'} for word in words):
        words.extend(['hello', 'thanks', 'welcome'])
    if any(word in {'collect', 'collects', 'collected'} for word in words):
        words.extend(['name', 'email', 'phone'])
    if any(word in {'book', 'books', 'booked', 'booking'} for word in words):
        words.extend(['appointment', 'scheduled', 'confirmed'])
    turns = [f'{speaker}: {body}' for speaker, body in _iter_transcript_turns(transcript)]
    matches = [turn for turn in turns if any(word in turn.lower() for word in words)]
    return matches[:2]


def _classify_failure_layer(criterion: str) -> tuple[str, str]:
    normalized = criterion.lower()
    layer_keywords = [
        ('stt_transcription', 'stt_transcription_gap', {'stt', 'transcription', 'transcript', 'misheard', 'heard'}),
        ('llm_instruction_following', 'llm_instruction_gap', {'instruction', 'intent', 'asked', 'answer', 'follow', 'handoff'}),
        ('tool_calls', 'tool_call_gap', {'tool', 'crm', 'calendar', 'book', 'schedule', 'scheduling', 'appointment', 'lookup'}),
        ('tts_timing', 'tts_timing_gap', {'tts', 'pause', 'silence', 'timing', 'delay', 'interruption', 'barge'}),
        ('latency', 'latency_gap', {'latency', 'slow', 'wait', 'seconds', 'timeout'}),
        ('policy_compliance', 'policy_compliance_gap', {'policy', 'compliance', 'consent', 'privacy', 'unsupported', 'claim'}),
        ('caller_behavior', 'caller_behavior_gap', {'caller', 'customer', 'user', 'angry', 'confused'}),
    ]

    words = set(re.findall(r'[a-zA-Z]+', normalized))
    for layer, tag, keywords in layer_keywords:
        if words & keywords:
            return layer, tag

    return 'conversation_quality', 'missing_conversation_evidence'


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')


def _artifact(artifact_id: str, artifact_type: str, value: str | dict[str, Any], source: str) -> EvalArtifact:
    data = value.encode('utf-8') if isinstance(value, str) else _canonical_json_bytes(value)
    return EvalArtifact(
        id=artifact_id,
        type=artifact_type,
        sha256=hashlib.sha256(data).hexdigest(),
        bytes=len(data),
        source=source,
    )


def _artifact_reference(artifact_id: str, artifact_type: str, source: str) -> dict[str, str]:
    return {
        'id': artifact_id,
        'type': artifact_type,
        'source': source,
        'digest_location': 'artifact_manifest',
    }



def _first_string(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_number(mapping: dict[str, Any], *keys: str) -> int | float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _voice_call_media_summary(call: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {}

    media = call.get('media') if isinstance(call.get('media'), dict) else {}
    metrics = call.get('metrics') if isinstance(call.get('metrics'), dict) else {}
    source = {**metrics, **media, **call}
    summary: dict[str, Any] = {}

    recording_url = _first_string(source, 'recording_url', 'recordingUrl', 'audio_url', 'audioUrl')
    if recording_url:
        summary['recording_url'] = recording_url

    recording_sha256 = _first_string(source, 'recording_sha256', 'recordingSha256', 'audio_sha256', 'audioSha256')
    if recording_sha256:
        summary['recording_sha256'] = recording_sha256

    mime_type = _first_string(source, 'mime_type', 'mimeType', 'content_type', 'contentType')
    if mime_type:
        summary['mime_type'] = mime_type

    duration_ms = _first_number(source, 'duration_ms', 'durationMs', 'call_duration_ms', 'callDurationMs')
    if duration_ms is not None:
        summary['duration_ms'] = duration_ms

    return summary


def _voice_call_metric_summary(call: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {}

    metrics = call.get('metrics') if isinstance(call.get('metrics'), dict) else {}
    quality = call.get('quality') if isinstance(call.get('quality'), dict) else {}
    source = {**quality, **metrics, **call}
    metric_fields = {
        'duration_ms': ('duration_ms', 'durationMs', 'call_duration_ms', 'callDurationMs'),
        'average_latency_ms': ('average_latency_ms', 'averageLatencyMs', 'avg_latency_ms', 'avgLatencyMs', 'latency_ms', 'latencyMs'),
        'max_latency_ms': ('max_latency_ms', 'maxLatencyMs', 'p95_latency_ms', 'p95LatencyMs'),
        'packet_loss_percent': ('packet_loss_percent', 'packetLossPercent'),
        'jitter_ms': ('jitter_ms', 'jitterMs'),
    }

    summary: dict[str, Any] = {}
    for output_key, input_keys in metric_fields.items():
        value = _first_number(source, *input_keys)
        if value is not None:
            summary[output_key] = value

    media = _voice_call_media_summary(call)
    if media:
        summary['media'] = media
    return summary


def _voice_interaction_summary(call: dict[str, Any] | None, transcript: str) -> dict[str, Any] | None:
    if not isinstance(call, dict):
        return None

    normalized = transcript.lower()
    summary = {
        'turn_count': len(_iter_transcript_turns(transcript)),
        'interruption_signal_count': sum(1 for phrase in ('interrupt', 'sorry to interrupt', 'let me stop you', 'hold on', 'go ahead', 'pause') if phrase in normalized),
        'correction_signal_count': sum(1 for phrase in ('actually', 'correction', 'corrected', 'instead', 'i meant', 'not the') if phrase in normalized),
        'handoff_signal_count': sum(1 for phrase in ('human', 'representative', 'escalate', 'transfer') if phrase in normalized),
    }
    summary.update(_voice_call_metric_summary(call))
    return summary


def _vcon_call_attachments(call: dict[str, Any] | None) -> list[dict[str, Any]]:
    media = _voice_call_media_summary(call)
    recording_url = media.get('recording_url')
    if not recording_url:
        return []

    attachment: dict[str, Any] = {
        'type': 'recording',
        'url': recording_url,
    }
    if media.get('mime_type'):
        attachment['mime_type'] = media['mime_type']
    if media.get('recording_sha256'):
        attachment['sha256'] = media['recording_sha256']
    if media.get('duration_ms') is not None:
        attachment['duration_ms'] = media['duration_ms']
    return [attachment]


def run_eval(payload: EvalRunRequest) -> EvalRunResponse:
    transcript, source_format, parsed_source = normalize_conversation(payload.conversation)
    voice_interaction_summary = _voice_interaction_summary(payload.call, transcript)
    criteria = _split_criteria(payload.criteria)
    checks: list[EvalCheck] = []
    created_at = datetime.now(UTC).isoformat()
    run_seed = f'{payload.criteria}\n{transcript}'.encode('utf-8')
    run_id = f'eval_{hashlib.sha256(run_seed).hexdigest()[:16]}'

    for criterion in criteria:
        evidence = _evidence_for(transcript, criterion)
        score = 82 if evidence else 48
        status = 'pass' if score >= 70 else 'needs_review'
        layer, root_cause_tag = _classify_failure_layer(criterion)
        checks.append(
            EvalCheck(
                name=criterion,
                status=status,
                score=score,
                layer=layer,
                root_cause_tag='none' if status == 'pass' else root_cause_tag,
                evidence=evidence,
                reason='Found supporting conversation evidence.' if evidence else 'No clear supporting evidence found in the provided call record.',
            )
        )

    overall_score = round(sum(item.score for item in checks) / len(checks)) if checks else 0
    verdict = 'pass' if overall_score >= 75 else 'needs_review'
    missing = [item.name for item in checks if item.status != 'pass']
    risk_flags = [f'{item.layer}: {item.name}' for item in checks if item.status != 'pass'][:4]
    suggested_fixes = [
        f'Add a clearer agent behavior for: {name}' for name in missing[:4]
    ] or ['Keep this eval in the regression suite and compare future calls against it.']
    analysis_id = hashlib.sha256(f'{payload.criteria}\n{transcript}'.encode('utf-8')).hexdigest()[:16]

    report_body: dict[str, Any] = {
        'id': analysis_id,
        'run_id': run_id,
        'title': payload.title or 'Voice AI call eval',
        'created_at': created_at,
        'overall_score': overall_score,
        'verdict': verdict,
        'checks': [item.model_dump() for item in checks],
        'risk_flags': risk_flags,
        'suggested_fixes': suggested_fixes,
    }
    if voice_interaction_summary is not None:
        report_body['voice_interaction_summary'] = voice_interaction_summary

    source_artifacts = [
        _artifact('input_transcript', 'transcript', transcript, source_format),
        _artifact('eval_criteria', 'criteria', payload.criteria, 'request'),
    ]
    if payload.call is not None:
        source_artifacts.append(_artifact('voice_call_artifact', 'call', payload.call, 'request'))
    audit_events = [
        EvalAuditEvent(
            event_type='eval.run.created',
            actor='system',
            at=created_at,
            summary=f'Created deterministic eval run from {source_format} input.',
            artifact_ids=[item.id for item in source_artifacts] + ['deterministic_report'],
        )
    ]

    report_body['artifact_manifest'] = [
        *[item.model_dump() for item in source_artifacts],
        _artifact_reference('deterministic_report', 'eval_report', 'eval_service'),
    ]
    report_body['audit_events'] = [item.model_dump() for item in audit_events]
    report_artifact = _artifact('deterministic_report', 'eval_report', report_body, 'eval_service')
    artifact_manifest = [
        *source_artifacts,
        report_artifact,
    ]

    vcon_analysis = {
        'type': 'voice_ai_eval',
        'encoding': 'json',
        'body': report_body,
    }

    if parsed_source is not None:
        vcon_analysis['source'] = {
            'format': source_format,
            'has_existing_analysis': bool(isinstance(parsed_source.get('analysis'), list)) if isinstance(parsed_source, dict) else False,
        }

    vcon_export = _build_vcon_export(transcript, source_format, parsed_source, vcon_analysis, payload.call)

    return EvalRunResponse(
        run_id=run_id,
        created_at=created_at,
        title=payload.title or 'Voice AI call eval',
        source_format=source_format,
        overall_score=overall_score,
        verdict=verdict,
        checks=checks,
        risk_flags=risk_flags,
        suggested_fixes=suggested_fixes,
        transcript_preview=transcript[:700],
        voice_interaction_summary=voice_interaction_summary,
        artifact_manifest=artifact_manifest,
        audit_events=audit_events,
        vcon_analysis=vcon_analysis,
        vcon_export=vcon_export,
    )
