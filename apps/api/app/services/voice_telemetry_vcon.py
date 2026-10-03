"""Preserve native voice telemetry and explicitly project observations into vCon.

No SDK, collector or remote fetch is needed. Provider payloads remain ancillary
files; they are not vCon core objects and are not proof of business state.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.services.vcon_evidence import (
    MAX_EVENTS, MAX_PROFILE_BYTES, _json, _status, attach_evidence,
    build_benchmark_vcon, decode_evidence, redact,
)

FORMATS = {'otlp-json-v1', 'pipecat-function-events-v1', 'livekit-session-report-v1', 'vapi-call-v1'}


def _timestamp(value: Any, divisor: int = 1) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / divisor, UTC).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        raise ValueError('Invalid native telemetry timestamp') from None


def _structured(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _arguments(value: Any) -> dict[str, Any]:
    parsed = _structured(value)
    return parsed if isinstance(parsed, dict) else {}


def _event(raw: dict[str, Any], index: int, source: str, path: str) -> dict[str, Any]:
    return {'event_id': f'{source}-{hashlib.sha256(_json([index, raw]).encode()).hexdigest()[:24]}',
            'source': source, 'source_path': path, 'sequence': index + 1}


def _any(value: Any) -> Any:
    if not isinstance(value, dict):
        raise ValueError('OTLP attributes require AnyValue objects')
    for key in ('stringValue', 'boolValue', 'intValue', 'doubleValue', 'bytesValue'):
        if key in value:
            return value[key]
    if 'arrayValue' in value:
        return [_any(v) for v in value['arrayValue'].get('values', [])]
    if 'kvlistValue' in value:
        return _attributes(value['kvlistValue'].get('values', []))
    return None


def _attributes(values: Any) -> dict[str, Any]:
    if not isinstance(values, list):
        raise ValueError('OTLP attributes must be an array')
    return {v['key']: _any(v['value']) for v in values}


def _otlp(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    tools, voice = [], []
    if not isinstance(data.get('resourceSpans'), list):
        raise ValueError('Expected an OTLP JSON ExportTraceServiceRequest')
    for resource_index, resource in enumerate(data['resourceSpans']):
        for scope_index, scope in enumerate(resource.get('scopeSpans', [])):
            for span_index, span in enumerate(scope.get('spans', [])):
                index = len(tools) + len(voice)
                if index >= MAX_EVENTS:
                    raise ValueError('Native telemetry exceeds the event limit')
                attrs = _attributes(span.get('attributes', []))
                path = f'/resourceSpans/{resource_index}/scopeSpans/{scope_index}/spans/{span_index}'
                common = _event(span, index, 'opentelemetry', path)
                common.update({key: span[otlp] for key, otlp in
                               [('trace_id', 'traceId'), ('span_id', 'spanId'), ('parent_span_id', 'parentSpanId')]
                               if span.get(otlp)})
                common['timestamp'] = _timestamp(span.get('startTimeUnixNano'), 1_000_000_000)
                common['started_at'] = common['timestamp']
                observed_nano = span.get('endTimeUnixNano') or span.get('startTimeUnixNano')
                if observed_nano:
                    common['observed_time_unix_nano'] = str(observed_nano)
                if span.get('startTimeUnixNano') and span.get('endTimeUnixNano'):
                    common['duration_ms'] = (int(span['endTimeUnixNano']) - int(span['startTimeUnixNano'])) / 1_000_000
                    if common['duration_ms'] < 0:
                        raise ValueError('Native span ends before it starts')
                name = attrs.get('gen_ai.tool.name') or attrs.get('tool.function_name')
                # Never treat a tool definition or an LLM's requested tool as execution.
                operation = attrs.get('gen_ai.operation.name')
                if name and (operation == 'execute_tool' or span.get('name') in {'llm_tool_call', 'llm_tool_result'}):
                    call_id = attrs.get('gen_ai.tool.call.id') or attrs.get('tool.call_id')
                    if not call_id:
                        raise ValueError('Native tool execution requires an invocation ID')
                    args = attrs.get('gen_ai.tool.call.arguments', attrs.get('tool.arguments'))
                    result = attrs.get('gen_ai.tool.call.result', attrs.get('tool.result'))
                    code = (span.get('status') or {}).get('code')
                    explicit = attrs.get('tool.result_status')
                    status = _status(explicit) if 'tool.result_status' in attrs else 'error' if code in {2, 'STATUS_CODE_ERROR'} else (
                        'success' if code in {1, 'STATUS_CODE_OK'} and result is not None else 'unknown')
                    requested = span.get('name') == 'llm_tool_call'
                    common['timestamp'] = _timestamp(observed_nano, 1_000_000_000)
                    tools.append({**common, 'event_type': 'tool_call.requested' if requested else 'tool_call.result',
                                  'name': str(name), 'call_id': str(call_id), 'arguments': _arguments(args),
                                  'result': _structured(result), 'status': 'requested' if requested else status,
                                  'attributes': attrs})
                else:
                    measurements = [{'name': 'ttfb', 'value': attrs['metrics.ttfb'], 'unit': 's'}] if 'metrics.ttfb' in attrs else []
                    voice.append({**common, 'event_type': 'pipeline.span', 'name': span.get('name'),
                                  'attributes': attrs, 'measurements': measurements})
    # An OTLP batch is not capture-ordered. Use reported observation times for
    # related lifecycle spans; reject missing order rather than guessing.
    if tools and all(e.get('observed_time_unix_nano') for e in tools):
        tools.sort(key=lambda e: int(e['observed_time_unix_nano']))
    elif len({e['call_id'] for e in tools}) != len(tools):
        raise ValueError('Related native lifecycle spans require observation times')
    arguments: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(tools):
        event['sequence'] = index + 1
        if event['arguments']:
            arguments[event['call_id']] = event['arguments']
        else:
            event['arguments'] = arguments.get(event['call_id'], {})
    return tools, voice, ''


def _pipecat(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    events = data.get('events')
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError('Expected a bounded Pipecat FunctionCallEvent events array')
    outcomes = {'function_call_started': ('tool_call.requested', 'requested'),
                'function_call_in_progress': ('tool_call.started', 'running'),
                'function_call_completed': ('tool_call.result', 'success'),
                'function_call_failed': ('tool_call.result', 'error'),
                'function_call_timed_out': ('tool_call.result', 'timeout'),
                'function_call_cancelled': ('tool_call.result', 'cancelled')}
    tools, arguments = [], {}
    for index, event in enumerate(events):
        if event.get('kind') not in outcomes or not event.get('tool_call_id') or not event.get('function_name'):
            raise ValueError('Unrecognized Pipecat function lifecycle event')
        call_id = str(event['tool_call_id'])
        if event.get('arguments') is not None:
            arguments[call_id] = _arguments(event['arguments'])
        event_type, status = outcomes[event['kind']]
        tools.append({**_event(event, index, 'pipecat', f'/events/{index}'),
                      'event_type': event_type, 'call_id': call_id, 'name': event['function_name'],
                      'timestamp': _timestamp(event.get('timestamp')), 'arguments': arguments.get(call_id, {}),
                      'result': event.get('result'), 'status': status,
                      'attributes': {key: event[key] for key in ('group_id', 'blocking', 'error', 'started_at', 'in_progress_at') if key in event}})
    return tools, [], ''


def _livekit(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    # SessionReport.to_dict() from the Python Agents SDK; Node uses different units/keys.
    items = (data.get('chat_history') or {}).get('items')
    if not isinstance(items, list) or len(items) > MAX_EVENTS:
        raise ValueError('Expected a bounded LiveKit Python SessionReport chat_history.items array')
    tools, voice, lines, requests = [], [], [], {}
    for index, item in enumerate(items):
        common = _event(item, index, 'livekit', f'/chat_history/items/{index}')
        common['timestamp'] = _timestamp(item.get('created_at'))
        if item.get('type') == 'function_call':
            if not item.get('call_id') or not item.get('name'):
                raise ValueError('LiveKit function call requires name and invocation ID')
            event = {**common, 'call_id': item['call_id'], 'name': item['name'],
                     'arguments': _arguments(item.get('arguments')), 'result': None,
                     'event_type': 'tool_call.requested', 'status': 'requested'}
            requests[item['call_id']] = event
            tools.append(event)
        elif item.get('type') == 'function_call_output':
            previous = requests.get(item.get('call_id'))
            if not previous or type(item.get('is_error')) is not bool or 'output' not in item:
                raise ValueError('LiveKit result requires matching invocation and explicit is_error')
            tools.append({**previous, **common, 'event_type': 'tool_call.result',
                          'status': 'error' if item['is_error'] else 'success', 'result': _structured(item.get('output'))})
        else:
            voice.append({**common, 'event_type': 'conversation.observation', 'attributes': item,
                          'measurement_units': {key: 's' for key in (item.get('metrics') or {}) if key in {
                              'transcription_delay', 'end_of_turn_delay', 'llm_node_ttft', 'tts_node_ttfb',
                              'e2e_latency', 'playback_latency', 'on_user_turn_completed_delay'}}})
            if item.get('type') == 'message' and item.get('role') in {'user', 'assistant'}:
                text = '\n'.join(c for c in item.get('content', []) if isinstance(c, str))
                if text:
                    lines.append(f'{"Caller" if item["role"] == "user" else "Agent"}: {text}')
    return tools, voice, '\n'.join(lines)


def _vapi(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    artifact = data.get('artifact') or {}
    messages = artifact.get('messages') or []
    if not isinstance(messages, list) or len(messages) > MAX_EVENTS or not isinstance(artifact, dict):
        raise ValueError('Expected a bounded Vapi call artifact')
    # Vendor successEvaluation/structured outputs are derived analysis, not
    # tool execution or an externally observed business-state snapshot.
    voice = [{**_event(item, i, 'vapi', f'/artifact/messages/{i}'),
              'event_type': 'conversation.observation', 'attributes': item}
             for i, item in enumerate(messages)]
    return [], voice, str(artifact.get('transcript') or '')


def build_telemetry_vcon(*, format: str, data: dict[str, Any], transcript: str = '',
                         suite_id: str | None = None, scenario_id: str | None = None,
                         final_state: dict[str, Any] | None = None, synthetic: bool = False) -> dict[str, Any]:
    if format not in FORMATS or len(_json(data).encode()) > MAX_PROFILE_BYTES:
        raise ValueError('Unsupported or oversized source telemetry (2 MB maximum)')
    try:
        tools, voice, native_transcript = {'otlp-json-v1': _otlp, 'pipecat-function-events-v1': _pipecat,
                                         'livekit-session-report-v1': _livekit, 'vapi-call-v1': _vapi}[format](data)
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValueError('Malformed source telemetry') from exc
    if transcript and native_transcript and transcript != native_transcript:
        raise ValueError('Supplied transcript conflicts with native session history')
    transcript = transcript or native_transcript
    exported = build_benchmark_vcon({'suite_id': suite_id, 'scenario_id': scenario_id, 'final_state': final_state}, transcript, synthetic=synthetic)
    body = decode_evidence(exported)
    assert body is not None
    removed: list[str] = []
    body['tool_events'] = redact(tools, '/tool_events', removed)
    body['voice_events'] = redact(voice, '/voice_events', removed)
    body['context']['source_format'] = format
    native = redact(data, '/source_telemetry', removed)
    # Never make a producer's private filesystem path a portable media reference.
    if 'audio_recording_path' in native:
        native.pop('audio_recording_path')
        removed.append('/source_telemetry/audio_recording_path')
    body['redactions'].extend(removed)
    exported = attach_evidence(exported, body)
    owner = next(i for i, party in enumerate(exported['parties']) if party['name'] == 'ConVoice QA')
    exported['attachments'].append({'purpose': f'Source telemetry ({format})', 'start': exported['created_at'],
                                    'party': owner, 'mediatype': 'application/json', 'encoding': 'json', 'body': native})
    return exported


def attach_source_telemetry(vcon: dict[str, Any], telemetry: dict[str, Any]) -> dict[str, Any]:
    """Common adapter seam: keep native evidence without duplicating actions."""
    native = build_telemetry_vcon(format=telemetry.get('format', ''), data=telemetry.get('data', {}))
    body = decode_evidence(vcon)
    projected = decode_evidence(native)
    if body is None or projected is None:
        raise ValueError('Source telemetry requires the execution evidence profile')
    # The adapter's explicit action trace is authoritative for its contract.
    # Native tools fill a missing trace, but are never blended/double-counted.
    if not body['tool_events']:
        body['tool_events'] = projected['tool_events']
    body['voice_events'].extend(projected['voice_events'])
    body['redactions'].extend(projected['redactions'])
    body['context']['source_format'] = telemetry['format']
    exported = attach_evidence(vcon, body)
    attachment = native['attachments'][-1]
    attachment['party'] = next(i for i, party in enumerate(exported['parties']) if party['name'] == 'ConVoice QA')
    exported['attachments'].append(attachment)
    return exported
