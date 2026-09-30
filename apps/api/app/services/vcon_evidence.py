"""CAE's versioned execution-evidence profile inside standard vCon attachments.

The profile describes reported observations, not proof that an imported source
is trustworthy. Evaluation output is deliberately never used as input evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.services.assert_trace import parse_action_trace
from app.services.vcon_interop import IETF_VCON_VERSION, vcon_dialog_turns

PROFILE = 'cae-execution-evidence-v1'
PURPOSE = 'CAE execution evidence'
MAX_PROFILE_BYTES = 2_000_000
MAX_EVENTS = 10_000
SECRET_KEYS = re.compile(r'^(authorization|cookie|password|passwd|secret|.*token|.*api[_-]?key|credential.*|runtime_provenance|internal.*)$', re.I)
SUCCESS = {'success', 'succeeded', 'completed', 'complete', 'ok', 'passed', 'true'}
FAILURE = {'error', 'failed', 'failure', 'fail', 'errored', 'false'}


def _json(value: Any) -> str:
    def keys(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(k): keys(v) for k, v in item.items()}
        if isinstance(item, list):
            return [keys(v) for v in item]
        return item
    return json.dumps(keys(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def redact(value: Any, path: str = '', removed: list[str] | None = None) -> Any:
    """Remove credential/runtime fields; keep business evidence and record losses."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            location = f'{path}/{key}'
            if SECRET_KEYS.match(str(key)):
                if removed is not None:
                    removed.append(location)
                continue
            result[key] = redact(item, location, removed)
        return result
    if isinstance(value, list):
        return [redact(item, f'{path}/{index}', removed) for index, item in enumerate(value)]
    return deepcopy(value)


def _status(raw: Any, event_type: str = '') -> str:
    status = str(raw or '').lower().replace('-', '_').replace(' ', '_')
    if event_type in {'tool_call.requested', 'tool_call.started'}:
        return 'requested' if event_type.endswith('requested') else 'running'
    if status in SUCCESS:
        return 'success'
    if status in FAILURE:
        return 'error'
    if status in {'canceled', 'cancelled'}:
        return 'cancelled'
    if status in {'timeout', 'timed_out'}:
        return 'timeout'
    return status if status in {'requested', 'running', 'pending', 'unknown'} else 'unknown'


def tool_events(action_trace: Any, *, source: str) -> list[dict[str, Any]]:
    events = []
    for index, action in enumerate(parse_action_trace(action_trace)):
        raw = action.raw if isinstance(action.raw, dict) else {}
        event_type = str(raw.get('event_type') or '')
        identity = str(raw.get('event_id') or hashlib.sha256(_json([index, raw]).encode()).hexdigest()[:24])
        event = {
            'event_id': identity, 'sequence': raw.get('sequence', index + 1),
            'event_type': event_type or 'tool_call.result',
            'call_id': str(raw.get('call_id') or raw.get('tool_call_id') or identity),
            'name': action.name, 'arguments': action.arguments, 'result': action.result,
            'status': _status(action.status, event_type), 'source': str(raw.get('source') or source),
        }
        for key in ('started_at', 'completed_at', 'timestamp', 'duration_ms', 'turn_index', 'dialog',
                    'retry_of', 'trace_id', 'span_id', 'parent_span_id'):
            if key in raw:
                event[key] = raw[key]
        events.append(event)
    return events


def capture_voice_events(turns: list[Any], live_events: list[Any], latency_marks: list[Any]) -> list[dict[str, Any]]:
    """Capture only observations supplied by an adapter, without inventing playback."""
    events: list[dict[str, Any]] = []
    for index, raw in enumerate(turns):
        turn = raw.model_dump(mode='json') if hasattr(raw, 'model_dump') else raw
        if not isinstance(turn, dict):
            continue
        meta = turn.get('frame_metadata') if isinstance(turn.get('frame_metadata'), dict) else {}
        for event_type, value in (
            ('llm.output', meta.get('source_text') or turn.get('text')),
            ('asr.receipt', meta.get('asr_receipt')),
        ):
            if value:
                events.append({'event_id': f'turn-{index + 1}-{event_type}', 'event_type': event_type,
                               'turn_index': turn.get('turn_index', index + 1), 'speaker': turn.get('speaker'),
                               'text': value, 'source': 'execution_adapter'})
        if meta:
            events.append({'event_id': f'turn-{index + 1}-speech', 'event_type': 'speech.observation',
                           'turn_index': turn.get('turn_index', index + 1), 'attributes': meta,
                           'source': 'execution_adapter'})
    for index, raw in enumerate(live_events):
        event = raw.model_dump(mode='json') if hasattr(raw, 'model_dump') else raw
        if isinstance(event, dict):
            # Emission of media to a listener does not prove browser playout.
            events.append({'event_id': f'live-{event.get("sequence", index + 1)}',
                           'event_type': 'audio.available' if event.get('kind') == 'audio' else 'message.observed',
                           'timestamp': event.get('created_at'), 'source': 'execution_adapter',
                           'attributes': event})
    for index, mark in enumerate(latency_marks):
        if isinstance(mark, dict):
            events.append({'event_id': f'latency-{index + 1}', 'event_type': 'latency.observation',
                           'source': 'execution_adapter', 'attributes': mark})
    return events


def evidence_body(*, action_trace: Any = None, observed_actions: list[str] | None = None,
                  final_state: Any = None, voice_events: list[Any] | None = None,
                  state_snapshots: list[dict[str, Any]] | None = None,
                  context: dict[str, Any] | None = None, synthetic: bool = False) -> dict[str, Any]:
    context = context or {}
    source = 'synthetic_fixture' if synthetic else 'reported_observation'
    state = final_state if isinstance(final_state, dict) else {}
    # CAE bookkeeping is not externally observed business state.
    if state.get('outcome') in {'conversation_only_evidence_recorded', 'reference_conversation_captured'}:
        state = {}
    snapshots = deepcopy(state_snapshots or [])
    if state:
        existing_final = [s for s in snapshots if s.get('phase') == 'final']
        if existing_final and any(_json(s.get('state')) != _json(state) for s in existing_final):
            raise ValueError('Conflicting captured final state snapshots')
        if not existing_final:
            snapshots.append({'snapshot_id': 'final', 'phase': 'final', 'source': source,
                              'observed_at': context.get('completed_at'), 'state': state})
    body = {
        'schema': PROFILE, 'synthetic': synthetic, 'context': context,
        'tool_events': tool_events(action_trace, source=source),
        'observed_actions': [str(a) for a in observed_actions] if isinstance(observed_actions, list) else [],
        'state_snapshots': snapshots,
        'voice_events': voice_events or [],
    }
    removed: list[str] = []
    body = redact(body, removed=removed)
    body['redactions'] = removed
    return body


def attach_evidence(vcon: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    exported = deepcopy(vcon)
    attachments = exported.setdefault('attachments', [])
    parties = exported.setdefault('parties', [])
    exporter_party = next((i for i, p in enumerate(parties) if p.get('name') == 'ConVoice QA'), None)
    if exporter_party is None:
        exporter_party = len(parties)
        parties.append({'name': 'ConVoice QA', 'type': 'bot', 'validation': 'none'})
    # Replace only our profile. Preserve unrelated attachments/analysis and indices.
    item = {'purpose': PURPOSE, 'start': exported.get('updated_at') or exported['created_at'], 'party': exporter_party,
            'mediatype': 'application/json', 'encoding': 'json', 'body': body}
    if exported.get('dialog'):
        item['dialog'] = 0  # profile applies to the conversation; events may refine the reference
    existing = [i for i, value in enumerate(attachments) if isinstance(value, dict)
                and value.get('purpose') == PURPOSE]
    if len(existing) > 1:
        raise ValueError('Multiple CAE execution evidence attachments are ambiguous')
    if existing:
        attachments[existing[0]] = item
    else:
        attachments.append(item)
    decode_evidence(exported)
    return exported


def decode_evidence(vcon: dict[str, Any]) -> dict[str, Any] | None:
    if vcon.get('critical'):
        raise ValueError('Unsupported critical vCon extensions; evidence cannot be interpreted safely')
    if 'attachments' in vcon and not isinstance(vcon['attachments'], list):
        raise ValueError('vCon attachments must be an array')
    found = []
    for item in vcon.get('attachments') or []:
        if not isinstance(item, dict) or item.get('purpose') != PURPOSE:
            continue
        if vcon.get('vcon') != IETF_VCON_VERSION:
            raise ValueError('Execution evidence requires vCon format 0.4.0')
        if item.get('mediatype') != 'application/json' or type(item.get('party')) is not int or not 0 <= item['party'] < len(vcon.get('parties') or []):
            raise ValueError('Invalid execution evidence attachment provenance')
        if 'dialog' in item and (type(item['dialog']) is not int or not 0 <= item['dialog'] < len(vcon.get('dialog') or [])):
            raise ValueError('Invalid execution evidence attachment dialog')
        body = item.get('body')
        if item.get('encoding') != 'json' or not isinstance(body, dict) or body.get('schema') != PROFILE:
            raise ValueError('Unsupported or malformed CAE execution evidence profile')
        if len(_json(body).encode()) > MAX_PROFILE_BYTES:
            raise ValueError('CAE execution evidence exceeds the 2 MB limit')
        for field in ('tool_events', 'state_snapshots', 'voice_events', 'observed_actions', 'redactions'):
            if not isinstance(body.get(field), list) or len(body[field]) > MAX_EVENTS:
                raise ValueError(f'Invalid CAE evidence {field}')
        if not isinstance(body.get('context'), dict) or not isinstance(body.get('synthetic'), bool):
            raise ValueError('Invalid CAE evidence context or synthetic marker')
        if any(not isinstance(path, str) for path in body['redactions']):
            raise ValueError('Redaction paths must be strings')
        seen = set()
        for event in [*body['tool_events'], *body['voice_events']]:
            if not isinstance(event, dict) or not isinstance(event.get('event_id'), str) or not event['event_id']:
                raise ValueError('Evidence events require an event_id')
            if not isinstance(event.get('event_type'), str) or not event['event_type'] or not isinstance(event.get('source'), str):
                raise ValueError('Evidence events require event type and source')
            if event['event_id'] in seen:
                raise ValueError('Duplicate evidence event_id')
            seen.add(event['event_id'])
            if 'dialog' in event and (type(event['dialog']) is not int or not 0 <= event['dialog'] < len(vcon.get('dialog') or [])):
                raise ValueError('Evidence event references an invalid dialog')
        for event in body['tool_events']:
            if not isinstance(event.get('name'), str) or not event['name'] or not isinstance(event.get('call_id'), str) or not event['call_id']:
                raise ValueError('Tool events require a name and call_id')
            if type(event.get('sequence')) is not int or event['sequence'] < 1:
                raise ValueError('Tool events require a positive capture sequence')
            if event.get('status') not in {'success', 'error', 'cancelled', 'timeout', 'requested', 'running', 'pending', 'unknown'}:
                raise ValueError('Invalid tool event status')
            if not isinstance(event.get('arguments'), dict) or not isinstance(event.get('source'), str):
                raise ValueError('Tool events require argument and source metadata')
            if 'result' not in event:
                raise ValueError('Tool events require a result field (null until observed)')
            if event.get('event_type') in {'tool_call.requested', 'tool_call.started'} and event['status'] == 'success':
                raise ValueError('A tool request cannot assert a successful result')
        if len({e['sequence'] for e in body['tool_events']}) != len(body['tool_events']):
            raise ValueError('Duplicate tool event sequence')
        finals = [snapshot for snapshot in body['state_snapshots'] if isinstance(snapshot, dict) and snapshot.get('phase') == 'final']
        if len(finals) > 1 or any(not isinstance(s, dict) or not isinstance(s.get('state'), dict) for s in body['state_snapshots']):
            raise ValueError('Invalid or ambiguous state snapshots')
        if any(not isinstance(s.get('snapshot_id'), str) or not s['snapshot_id'] or
               s.get('phase') not in {'before', 'after', 'final'} or not isinstance(s.get('source'), str)
               for s in body['state_snapshots']):
            raise ValueError('State snapshots require identity, phase and source')
        if len({s['snapshot_id'] for s in body['state_snapshots']}) != len(body['state_snapshots']):
            raise ValueError('Duplicate state snapshot_id')
        if any(not isinstance(a, str) for a in body['observed_actions']):
            raise ValueError('Observed actions must be strings')
        found.append(body)
    if len(found) > 1:
        raise ValueError('Multiple CAE execution evidence attachments are ambiguous')
    return deepcopy(found[0]) if found else None


def intake_vcon(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use known raw evidence only; never promote stored scores to observations."""
    normalized = deepcopy(payload)
    vcon = payload.get('vcon')
    if not isinstance(vcon, dict):
        return normalized, {}
    body = decode_evidence(vcon)
    text = '\n'.join(vcon_dialog_turns(vcon))
    recovered: dict[str, Any] = {'transcript': text}
    for snake, camel in (('suite_id', 'suiteId'), ('scenario_id', 'scenarioId')):
        if normalized.get(camel) and not normalized.get(snake):
            normalized[snake] = normalized[camel]
    if body:
        context = body['context']
        for key in ('suite_id', 'scenario_id'):
            if context.get(key):
                recovered[key] = context[key]
        events = body['tool_events']
        # Sequence is an explicit capture-order key. Do not guess from missing timestamps.
        if events and all(type(e.get('sequence')) is int for e in events):
            if len({e['sequence'] for e in events}) != len(events):
                raise ValueError('Duplicate tool event sequence')
            events.sort(key=lambda e: e['sequence'])
        # Each invocation contributes its latest observation to scoring. Retain
        # every lifecycle event in the attachment for the timeline.
        latest: dict[str, Any] = {}
        for event in events:
            latest[event['call_id']] = event
        recovered['action_trace'] = list(latest.values())
        recovered['observed_actions'] = body['observed_actions']
        recovered['final_state'] = next((s['state'] for s in body['state_snapshots'] if s.get('phase') == 'final'), {})
    for key, value in recovered.items():
        explicit = normalized.get(key)
        if key in {'action_trace', 'final_state'} and isinstance(explicit, str):
            try:
                explicit = json.loads(explicit)
            except ValueError as exc:
                raise ValueError(f'Invalid JSON in supplied {key}') from exc
            normalized[key] = explicit
        if explicit and (value or (body and key in {'transcript', 'action_trace', 'final_state', 'observed_actions'})) and _json(explicit) != _json(value):
            # UI normalizes JSON fields before submission; legacy direct traces are
            # compared through the same projection as exported tool events.
            equivalent = key == 'action_trace' and _json(tool_events(explicit, source='reported_observation')) == _json(value)
            if not equivalent:
                raise ValueError(f'Conflicting {key} evidence: supplied value differs from the vCon')
        if not explicit and value and (body or key != 'transcript'):
            normalized[key] = deepcopy(value)
    summary = {'profile': PROFILE if body else None, 'synthetic': bool(body and body['synthetic']),
               'tool_events': len(body['tool_events']) if body else 0,
               'state_snapshots': len(body['state_snapshots']) if body else 0,
               'voice_events': len(body['voice_events']) if body else 0,
               'redactions': body['redactions'] if body else [],
               'trust': 'source-reported; not independently authenticated',
               'evidence_level': 'state_observed' if body and recovered.get('final_state') else
               'tools_observed' if body and body['tool_events'] else 'transcript_only'}
    return normalized, summary


def build_benchmark_vcon(payload: dict[str, Any], transcript: str, report: dict[str, Any] | None = None,
                         *, synthetic: bool = False) -> dict[str, Any]:
    report = report or {}
    original = payload.get('vcon')
    if isinstance(original, dict) and original.get('vcon') == IETF_VCON_VERSION:
        exported = deepcopy(original)
    else:
        dialog = []
        parties: list[dict[str, str]] = []
        for line in transcript.splitlines():
            speaker, sep, text = line.partition(':')
            name = speaker.strip() if sep else 'Speaker'
            if not line.strip():
                continue
            if name not in [p['name'] for p in parties]:
                parties.append({'name': name})
            dialog.append({'type': 'text', 'parties': [[p['name'] for p in parties].index(name)],
                           'mediatype': 'text/plain', 'encoding': 'none', 'body': text.strip() if sep else line})
        exported = {'vcon': IETF_VCON_VERSION, 'uuid': str(uuid.uuid4()),
                    'created_at': datetime.now(UTC).isoformat(), 'parties': parties or [{'name': 'CAE'}],
                    'dialog': dialog, 'analysis': []}
    existing_body = decode_evidence(exported)
    context = {key: report.get(key) or payload.get(key) for key in
               ('suite_id', 'scenario_id', 'scenario_contract_sha256', 'suite_contract_manifest_sha256')}
    if existing_body:
        body = existing_body  # preserve input source attribution and synthetic marker
    else:
        body = evidence_body(action_trace=payload.get('action_trace'), observed_actions=payload.get('observed_actions'),
                             final_state=payload.get('final_state'), context=context, synthetic=synthetic)
    exported = attach_evidence(exported, body)
    if report:
        exported.setdefault('analysis', []).append({
            'type': 'report', 'vendor': 'ConversationAgentEvals', 'product': 'ConVoice QA',
            'schema': 'cae-deterministic-evaluation-v1', 'mediatype': 'application/json', 'encoding': 'json',
            'dialog': list(range(len(exported['dialog']))),
            'attachment': next(i for i, a in enumerate(exported['attachments']) if a.get('purpose') == PURPOSE),
            'body': {'context': context, 'evaluator': 'assert-boundary', 'assert_version': '0.3.0',
                     'score': report.get('overall_score'), 'verdict': report.get('verdict'),
                     'scoring_mode': report.get('scoring_mode'), 'contract': report.get('scenario_contract'),
                     'evidence_citations': report.get('evidence_citations', []),
                     'semantic_review': 'separate; rerun results may vary'},
        })
    return exported
