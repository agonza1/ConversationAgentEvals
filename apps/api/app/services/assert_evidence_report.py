"""Bounded, exact evidence display metadata, never a second semantic evaluator.

Only the canonical judge input can supply jump targets. Model-authored paths,
URLs, repaired indices and approximate quotes never become navigable evidence.
"""
from __future__ import annotations

from html import unescape
import json
import re
from typing import Any

from app.integrations.assert_runtime import CITE_XML_PATTERN, Transcript, TranscriptEvent, TranscriptMetadata
from app.services.assert_transcript_adapter import (
    TRANSCRIPT_ADAPTER_VERSION, _has_unstructured_final_state_evidence, _structured_final_state,
)

MAX_EVENTS = 2_000
MAX_SNAPSHOT_CHARS = 512_000
MAX_REFERENCE_CHARS = 512_000
MAX_REFERENCES = 128
MAX_PARTS = 512
_EVENT_ID = re.compile(r'event:(?:0|[1-9]\d{0,5})\Z')


def _has_transcript_evidence(inference: dict[str, Any]) -> bool:
    """Count only message evidence actually emitted to the judge's target view."""
    events = inference.get('events')
    if not isinstance(events, list):
        return False
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get('view'), list) or 'target' not in event['view']:
            continue
        raw = event.get('raw')
        if isinstance(raw, dict) and 'cae_execution_error' in raw:
            continue
        edit = event.get('edit')
        if not isinstance(edit, dict) or edit.get('type') != 'add_message':
            continue
        message = edit.get('message')
        text = message.get('content') if isinstance(message, dict) else None
        if isinstance(text, str) and text.strip():
            return True
    return False


def build_evidence_report(
    inference: dict[str, Any], score: dict[str, Any], conversation: dict[str, Any],
) -> dict[str, Any]:
    """Retain whole strings within limits, or explicitly withhold partial UI data."""
    result: dict[str, Any] = {
        'transcript_adapter_version': TRANSCRIPT_ADAPTER_VERSION,
        'evidence_messages': [], 'evidence_references': [],
        'evidence_details_status': 'complete',
        'evidence_coverage': {
            'transcript': _has_transcript_evidence(inference),
            'tool_trace': any(isinstance(action, dict) and bool(action)
                              for action in conversation.get('action_trace') or []),
            'final_state': _structured_final_state(conversation) is not None
            or _has_unstructured_final_state_evidence(conversation.get('unstructured_final_state_evidence')),
        },
    }
    try:
        events = inference.get('events')
        if not isinstance(events, list) or len(events) > MAX_EVENTS:
            raise ValueError('The canonical event snapshot exceeds the evidence display limit.')
        # Bound native rendering work too, including tool edits that consume XML
        # indices but deliberately do not get an invented tool-text UI renderer.
        if len(json.dumps([event.get('edit') for event in events], ensure_ascii=False)) > MAX_SNAPSHOT_CHARS:
            raise ValueError('The canonical evidence snapshot exceeds the evidence display limit.')
        transcript = Transcript(
            metadata=TranscriptMetadata(
                kind=str(inference.get('type') or 'scenario'),
                test_case_id=str(inference.get('test_case_id') or ''),
                behavior=str(inference.get('behavior') or ''),
                target=str(inference.get('target') or ''),
                tester_model=str(inference.get('tester_model') or ''),
            ),
            events=[TranscriptEvent.model_validate(event) for event in events],
        )
        entries = transcript.collect_messages_with_ids('target')
        index_to_id = {str(index): message_id for index, (message_id, _) in enumerate(entries, start=1)}
        # A quote echoed in native tool evidence is not unique merely because
        # the UI only offers conversation-message jump targets. Reuse ASSERT's
        # actual renderer for all canonical sources; do not invent tool text.
        canonical_sources = [{'id': message_id, 'text': message.content}
                             for message_id, message in entries]
        if sum(len(row['text']) for row in canonical_sources) > MAX_SNAPSHOT_CHARS:
            raise ValueError('The rendered evidence snapshot exceeds the evidence display limit.')
        messages = []
        for event_index, event in enumerate(events):
            edit = event.get('edit') or {}
            if edit.get('type') != 'add_message' or 'target' not in event.get('view', []):
                continue
            message = edit.get('message') or {}
            text, role = message.get('content'), message.get('role')
            if isinstance(text, str) and isinstance(role, str):
                messages.append({'id': f'event:{event_index}', 'role': role, 'text': text})
        if sum(len(row['text']) for row in messages) > MAX_SNAPSHOT_CHARS:
            raise ValueError('The message snapshot exceeds the evidence display limit.')

        verdict = score.get('verdict') or {}
        references = _reference_rows(verdict)
        if len(references) > MAX_REFERENCES or sum(len(row['parts']) for row in references) > MAX_PARTS:
            raise ValueError('The references exceed the evidence display limit.')
        if len(json.dumps(references, ensure_ascii=False)) > MAX_REFERENCE_CHARS:
            raise ValueError('The references exceed the evidence display limit.')
        by_id = {message['id']: message for message in messages}
        for reference in references:
            reference['parts'] = [
                _verified_part(part, canonical_sources, by_id, index_to_id) for part in reference['parts']
            ]
        result['evidence_messages'] = messages
        result['evidence_references'] = references
        warnings = verdict.get('citation_warnings')
        if isinstance(warnings, list):
            warnings = [warning for warning in warnings if isinstance(warning, str)]
            if sum(map(len, warnings)) > MAX_REFERENCE_CHARS or len(warnings) > MAX_REFERENCES:
                raise ValueError('Native citation warnings exceed the evidence display limit.')
            if warnings:
                result['evidence_details_message'] = 'ASSERT citation warnings: ' + '; '.join(warnings)
    except (ValueError, TypeError, AttributeError, KeyError):
        # Display limits cannot erase a successfully completed native review or
        # provoke a second model request. Never return a silently clipped quote.
        result.update(
            evidence_messages=[], evidence_references=[], evidence_details_status='limited',
            evidence_details_message=(
                'Complete evidence details could not be represented within the display limits. '
                'The original ASSERT score and evidence artifacts remain unchanged.'
            ),
        )
    return result


def _reference_rows(verdict: dict[str, Any]) -> list[dict[str, Any]]:
    native = verdict.get('citations')
    if isinstance(native, list) and native:
        if len(native) > MAX_REFERENCES or len(json.dumps(native, ensure_ascii=False)) > MAX_REFERENCE_CHARS:
            raise ValueError('Too many citation details.')
        rows = []
        for position, citation in enumerate(native, start=1):
            if not isinstance(citation, dict):
                rows.append({'index': position, 'description': 'Unsupported citation record',
                             'parts': [{'quote': '', '_issue': 'No structured citation record was retained.'}]})
                continue
            parts = citation.get('parts')
            if not isinstance(parts, list) or not parts:
                parts = [{'_issue': 'No structured quote parts were retained.', 'quote': ''}]
            if len(parts) > MAX_PARTS:
                raise ValueError('Too many quote parts.')
            index = citation.get('index')
            rows.append({
                'index': index if isinstance(index, (str, int)) and not isinstance(index, bool) else position,
                'description': citation.get('description') if isinstance(citation.get('description'), str) else '',
                'parts': [dict(part) if isinstance(part, dict) else
                          {'quote': '', '_issue': 'Unsupported citation part.'} for part in parts],
            })
        return rows
    raw = native or verdict.get('evidence_citations') or verdict.get('highlights')
    if raw is None or raw == '' or raw == []:
        return []
    if not isinstance(raw, str):
        # Keep legacy values inspectable but do not guess which event they mean.
        text = json.dumps(raw, ensure_ascii=False)
        if len(text) > MAX_REFERENCE_CHARS:
            raise ValueError('Legacy citations exceed the display limit.')
        return [{'index': 'legacy', 'description': 'Unstructured evidence reference',
                 'parts': [{'quote': text, '_issue': 'No canonical message index was retained.'}]}]
    if len(raw) > MAX_REFERENCE_CHARS:
        raise ValueError('Citation text exceeds the display limit.')
    matches = list(CITE_XML_PATTERN.finditer(raw))
    if not matches:
        return [{'index': 'legacy', 'description': 'Unstructured evidence reference',
                 'parts': [{'quote': raw, '_issue': 'No canonical message index was retained.'}]}]
    if len(matches) > MAX_REFERENCES:
        raise ValueError('Too many citation records.')
    return [{
        'index': int(match.group(1)), 'description': unescape(match.group(3).strip()),
        'parts': [{'quote': part, 'claimed_message_index': match.group(2)}
                  for part in _quote_parts(unescape(match.group(4).strip()))],
    } for match in matches]


def _quote_parts(quote: str) -> list[str]:
    return [part.strip() for part in quote.split('[...]') if part.strip()] or [quote]


def _verified_part(
    part: dict[str, Any], canonical_sources: list[dict[str, str]], by_id: dict[str, dict[str, str]],
    index_to_id: dict[str, str],
) -> dict[str, str]:
    quote = part.get('quoted_text', part.get('quote', ''))
    result = {'quote': quote if isinstance(quote, str) else '', 'status': 'unresolved'}

    def unresolved(detail: str, *, ambiguous: bool = False) -> dict[str, str]:
        return {**result, 'status': 'ambiguous' if ambiguous else 'unresolved', 'detail': detail}

    if part.get('_issue'):
        return unresolved(str(part['_issue']))
    resolution = part.get('resolution')
    if isinstance(resolution, dict):
        if resolution.get('status') in {'ambiguous', 'unresolved'}:
            return unresolved(str(resolution.get('detail') or 'ASSERT could not resolve this reference.'),
                              ambiguous=resolution['status'] == 'ambiguous')
        if resolution.get('status') != 'resolved' or resolution.get('method') != 'exact':
            return unresolved('ASSERT did not retain an exact citation match; no verified jump is available.')
    elif 'resolution' in part:
        return unresolved('ASSERT retained malformed citation resolution metadata.')
    claimed = part.get('claimed_message_index')
    target = index_to_id.get(str(claimed)) if isinstance(claimed, (str, int)) and not isinstance(claimed, bool) else None
    native_id = part.get('message_id')
    if native_id is not None and (not isinstance(native_id, str) or not _EVENT_ID.fullmatch(native_id)):
        return unresolved('The saved reference does not identify a canonical evidence event.')
    if not target:
        return unresolved('The claimed message index is not present in the canonical judge input.')
    if native_id is not None and target != native_id:
        return unresolved('ASSERT repaired or mismatched the claimed message index; no verified jump is available.')
    matched_index = part.get('matched_message_index')
    if matched_index is not None and str(matched_index) != str(claimed):
        return unresolved('The claimed and matched ASSERT indices differ; no verified jump is available.')
    if target not in by_id:
        return unresolved('This reference targets tool evidence, not a supported conversation-message snapshot.')
    if not result['quote']:
        return unresolved('No exact quoted text was retained.')
    occurrences = []
    for message in canonical_sources:
        offset = 0
        while len(occurrences) < 2:
            found = message['text'].find(result['quote'], offset)
            if found < 0:
                break
            occurrences.append(message['id'])
            offset = found + 1
        if len(occurrences) >= 2:
            return unresolved('The exact quote occurs more than once in the evidence snapshot.', ambiguous=True)
    if occurrences != [target]:
        return unresolved('The exact quote does not match its claimed canonical message.')
    if 'position' in part:
        position = part['position']
        text = by_id[target]['text']
        if (not isinstance(position, list) or len(position) != 2
                or any(type(index) is not int for index in position)
                or not 0 <= position[0] < position[1] <= len(text)
                or text[position[0]:position[1]] != result['quote']):
            return unresolved('The native citation span does not match the exact quote in the snapshot.')
    return {**result, 'status': 'matched', 'message_id': target}
