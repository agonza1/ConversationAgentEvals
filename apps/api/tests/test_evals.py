import hashlib
import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _canonical_json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')


def test_run_eval_from_transcript():
    response = client.post(
        '/api/evals/run',
        json={
            'title': 'Appointment setter QA',
            'conversation': 'Agent: Hi, thanks for calling. Caller: I need an appointment. Agent: Can I get your name and email before I book that appointment?',
            'criteria': 'Agent greets the caller\nAgent collects name and email\nAgent books an appointment',
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['run_id'].startswith('eval_')
    assert payload['created_at']
    assert payload['title'] == 'Appointment setter QA'
    assert payload['source_format'] == 'transcript'
    assert payload['overall_score'] > 0
    assert len(payload['checks']) == 3
    assert payload['checks'][0]['status'] == 'pass'
    assert payload['checks'][0]['layer'] == 'caller_behavior'
    assert payload['checks'][0]['root_cause_tag'] == 'none'
    assert payload['vcon_analysis']['type'] == 'voice_ai_eval'
    assert payload['vcon_analysis']['body']['run_id'] == payload['run_id']
    body_manifest = payload['vcon_analysis']['body']['artifact_manifest']
    assert {artifact['id'] for artifact in body_manifest} == {'input_transcript', 'eval_criteria', 'deterministic_report'}
    assert body_manifest[-1] == {
        'id': 'deterministic_report',
        'type': 'eval_report',
        'source': 'eval_service',
        'digest_location': 'artifact_manifest',
    }
    assert payload['vcon_analysis']['body']['audit_events'] == payload['audit_events']
    assert payload['vcon_analysis']['body']['checks']
    assert payload['vcon_export']['analysis'][0]['type'] == 'voice_ai_eval'
    assert payload['vcon_export']['dialog'][0]['originator'] == 'Agent'
    assert payload['vcon_export']['dialog'][1]['originator'] == 'Caller'
    assert payload['vcon_export']['parties'] == [{'name': 'Agent'}, {'name': 'Caller'}]
    artifact_ids = {artifact['id'] for artifact in payload['artifact_manifest']}
    assert artifact_ids == {'input_transcript', 'eval_criteria', 'deterministic_report'}
    assert all(len(artifact['sha256']) == 64 for artifact in payload['artifact_manifest'])
    report_artifact = next(artifact for artifact in payload['artifact_manifest'] if artifact['id'] == 'deterministic_report')
    report_body_bytes = _canonical_json_bytes(payload['vcon_analysis']['body'])
    assert report_artifact['sha256'] == hashlib.sha256(report_body_bytes).hexdigest()
    assert report_artifact['bytes'] == len(report_body_bytes)
    body_artifact_ids = {artifact['id'] for artifact in payload['vcon_analysis']['body']['artifact_manifest']}
    for event in payload['vcon_analysis']['body']['audit_events']:
        assert set(event['artifact_ids']).issubset(body_artifact_ids)
    assert payload['audit_events'] == [
        {
            'event_type': 'eval.run.created',
            'actor': 'system',
            'at': payload['created_at'],
            'summary': 'Created deterministic eval run from transcript input.',
            'artifact_ids': ['input_transcript', 'eval_criteria', 'deterministic_report'],
        }
    ]


def test_run_eval_run_id_is_stable_for_identical_inputs():
    request_body = {
        'title': 'Appointment setter QA',
        'conversation': 'Agent: Hi. Caller: I need an appointment. Agent: I can book that now.',
        'criteria': 'Agent helps book an appointment',
    }

    first_response = client.post('/api/evals/run', json=request_body)
    second_response = client.post('/api/evals/run', json=request_body)

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text
    assert first_response.json()['run_id'] == second_response.json()['run_id']


def test_run_eval_from_vcon_like_json():
    response = client.post(
        '/api/evals/run',
        json={
            'conversation': {
                'parties': [{'name': 'Caller'}, {'name': 'Agent'}],
                'dialog': [
                    {'party': 1, 'body': 'Thanks for calling. I can help with scheduling.'},
                    {'party': 0, 'body': 'I need to reschedule my appointment.'},
                ],
            },
            'criteria': 'Agent helps with scheduling',
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['source_format'] == 'vcon'
    assert payload['checks'][0]['evidence']
    assert payload['checks'][0]['layer'] == 'tool_calls'
    assert payload['vcon_analysis']['source']['format'] == 'vcon'
    assert payload['vcon_export']['analysis'][0]['type'] == 'voice_ai_eval'
    assert payload['vcon_export']['dialog'][0]['body'] == 'Thanks for calling. I can help with scheduling.'

    valid_response = client.post(
        '/api/evals/run',
        json={
            'conversation': '{"parties":[{"name":"Caller"},{"name":"Agent"}],"dialog":[{"party":1,"body":"Thanks for calling. I can help with scheduling."},{"party":0,"body":"I need to reschedule my appointment."}]}',
            'criteria': 'Agent helps with scheduling',
        },
    )

    assert valid_response.status_code == 200, valid_response.text
    payload = valid_response.json()
    assert payload['source_format'] == 'vcon'
    assert payload['checks'][0]['evidence']
    assert payload['vcon_analysis']['source']['format'] == 'vcon'


def test_failed_eval_includes_root_cause_layer():
    response = client.post(
        '/api/evals/run',
        json={
            'conversation': 'Agent: Hello. Caller: I need pricing. Agent: Let me email you later.',
            'criteria': 'Agent obtains explicit privacy consent',
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['checks'][0]['status'] == 'needs_review'
    assert payload['checks'][0]['layer'] == 'policy_compliance'
    assert payload['checks'][0]['root_cause_tag'] == 'policy_compliance_gap'
    assert payload['risk_flags'] == ['policy_compliance: Agent obtains explicit privacy consent']
