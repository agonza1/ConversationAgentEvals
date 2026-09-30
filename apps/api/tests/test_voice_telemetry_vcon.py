import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.vcon_evidence import decode_evidence, intake_vcon
from app.services.voice_telemetry_vcon import build_telemetry_vcon


def test_pipecat_native_lifecycle_preserves_arguments_and_cancelled_outcomes():
    data = {'events': [
        {'kind': 'function_call_started', 'function_name': 'lookup', 'tool_call_id': 'a', 'timestamp': 1000, 'arguments': {'id': '1'}},
        {'kind': 'function_call_completed', 'function_name': 'lookup', 'tool_call_id': 'a', 'timestamp': 1001},
        {'kind': 'function_call_cancelled', 'function_name': 'route', 'tool_call_id': 'b', 'timestamp': 1002},
    ]}
    vcon = build_telemetry_vcon(format='pipecat-function-events-v1', data=data, transcript='Agent: Hello')
    assert vcon['attachments'][-1]['body'] == data
    loaded, _ = intake_vcon({'vcon': vcon})
    assert [e['status'] for e in loaded['action_trace']] == ['success', 'cancelled']
    assert loaded['action_trace'][0]['arguments'] == {'id': '1'}
    assert loaded['action_trace'][0]['timestamp'].startswith('1970-01-01T00:16:41')


def test_livekit_python_session_report_keeps_native_history_and_units():
    data = {'sdk_version': '1.7', 'job_id': 'job', 'audio_recording_path': 'C:/private/audio.wav',
            'chat_history': {'items': [
                {'type': 'message', 'role': 'user', 'content': ['Check my address'], 'created_at': 1000},
                {'type': 'function_call', 'id': 'f1', 'call_id': 'call', 'name': 'lookup', 'arguments': '{"account_id":"a","api_key":"secret"}', 'created_at': 1001},
                {'type': 'function_call_output', 'id': 'f2', 'call_id': 'call', 'output': '{"address":"new"}', 'is_error': False, 'created_at': 1002},
                {'type': 'message', 'role': 'assistant', 'content': ['Your address is new'], 'created_at': 1003,
                 'metrics': {'e2e_latency': 0.6, 'llm_node_tps': 20}},
            ]}}
    vcon = build_telemetry_vcon(format='livekit-session-report-v1', data=data)
    body = decode_evidence(vcon)
    loaded, _ = intake_vcon({'vcon': vcon})
    assert loaded['transcript'] == 'Caller: Check my address\nAgent: Your address is new'
    assert loaded['action_trace'][0]['arguments'] == {'account_id': 'a'}
    assert loaded['action_trace'][0]['result'] == {'address': 'new'}
    assert loaded['action_trace'][0]['status'] == 'success'
    assert body['voice_events'][-1]['measurement_units'] == {'e2e_latency': 's'}
    assert 'private' not in json.dumps(vcon)
    assert 'secret' not in json.dumps(vcon)
    assert body['state_snapshots'] == []  # tool output is not a final business snapshot


def otlp():
    attrs = [
        {'key': 'gen_ai.operation.name', 'value': {'stringValue': 'execute_tool'}},
        {'key': 'gen_ai.tool.name', 'value': {'stringValue': 'lookup'}},
        {'key': 'gen_ai.tool.call.id', 'value': {'stringValue': 'call'}},
        {'key': 'gen_ai.tool.call.arguments', 'value': {'stringValue': '{"id":"a","password":"secret"}'}},
        {'key': 'gen_ai.tool.call.result', 'value': {'stringValue': '{"found":true}'}},
        {'key': 'authorization', 'value': {'stringValue': 'secret'}},
    ]
    return {'resourceSpans': [{'scopeSpans': [{'spans': [
        {'traceId': 'a' * 32, 'spanId': 'b' * 16, 'parentSpanId': 'c' * 16, 'name': 'execute_tool',
         'startTimeUnixNano': '1000000000', 'endTimeUnixNano': '1500000000', 'status': {'code': 1}, 'attributes': attrs},
        {'name': 'tts', 'startTimeUnixNano': '2000000000', 'endTimeUnixNano': '2100000000',
         'attributes': [{'key': 'metrics.ttfb', 'value': {'doubleValue': 0.1}}]},
    ]}]}]}


def test_otlp_retains_wire_format_ids_and_does_not_infer_success_from_unset_status():
    data = otlp()
    vcon = build_telemetry_vcon(format='otlp-json-v1', data=data, transcript='Agent: found')
    body = decode_evidence(vcon)
    event = body['tool_events'][0]
    assert event['trace_id'] == 'a' * 32
    assert event['parent_span_id'] == 'c' * 16
    assert event['duration_ms'] == 500
    assert event['status'] == 'success'
    assert event['arguments'] == {'id': 'a'}
    assert body['voice_events'][0]['measurements'] == [{'name': 'ttfb', 'value': 0.1, 'unit': 's'}]
    assert 'resourceSpans' in vcon['attachments'][-1]['body']
    assert 'secret' not in json.dumps(vcon)
    data['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['status']['code'] = 0
    assert decode_evidence(build_telemetry_vcon(format='otlp-json-v1', data=data))['tool_events'][0]['status'] == 'unknown'


def test_vapi_analysis_is_not_business_state_or_action_success():
    data = {'id': 'call', 'artifact': {'transcript': 'Agent: Updated', 'messages': [{'role': 'bot', 'message': 'Updated'}]},
            'analysis': {'successEvaluation': 'true', 'structuredData': {'complete': True}}}
    body = decode_evidence(build_telemetry_vcon(format='vapi-call-v1', data=data))
    assert body['tool_events'] == []
    assert body['state_snapshots'] == []
    assert body['voice_events']


def test_native_formats_reject_malformed_data_and_conflicting_transcript():
    client = TestClient(app)
    assert client.post('/api/benchmarks/evidence/telemetry-vcon', json={'format': 'otlp-json-v1', 'data': {}}).status_code == 422
    assert client.post('/api/benchmarks/evidence/telemetry-vcon', json={'format': 'unknown', 'data': {}}).status_code == 422
    bad = deepcopy(otlp())
    bad['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['endTimeUnixNano'] = '1'
    with pytest.raises(ValueError, match='before'):
        build_telemetry_vcon(format='otlp-json-v1', data=bad)


def test_common_execution_export_accepts_native_telemetry_without_double_counting():
    from app.services.execution_vcon import build_ietf_execution_vcon
    args = dict(conversation_id='c', execution_run_id='e', suite_id='s', scenario_id='x', scenario_title=None,
                mode='pipecat_webrtc', turns=[{'text': 'Hello', 'speaker': 'agent'}], recording=None,
                created_at='2026-09-30T12:00:00Z', updated_at='2026-09-30T12:01:00Z',
                source_telemetry={'format': 'otlp-json-v1', 'data': otlp()})
    vcon = build_ietf_execution_vcon(**args)
    body = decode_evidence(vcon)
    assert body['tool_events'][0]['call_id'] == 'call'
    assert vcon['attachments'][-1]['body']['resourceSpans']
    args['action_trace'] = [{'action': 'adapter_action', 'status': 'success', 'call_id': 'adapter-call'}]
    body = decode_evidence(build_ietf_execution_vcon(**args))
    assert len(body['tool_events']) == 1
    assert body['tool_events'][0]['call_id'] == 'adapter-call'


def test_otlp_batch_order_does_not_override_terminal_lifecycle_result():
    data = otlp()
    result = data['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
    result['name'] = 'llm_tool_result'
    request = deepcopy(result)
    request['name'] = 'llm_tool_call'
    request['spanId'] = 'd' * 16
    request['startTimeUnixNano'] = '100'
    request['endTimeUnixNano'] = '200'
    # OTLP exporter batches can put the completed span before the request span.
    data['resourceSpans'][0]['scopeSpans'][0]['spans'].append(request)
    vcon = build_telemetry_vcon(format='otlp-json-v1', data=data)
    loaded, _ = intake_vcon({'vcon': vcon})
    assert loaded['action_trace'][0]['status'] == 'success'
