from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


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
    assert payload['title'] == 'Appointment setter QA'
    assert payload['source_format'] == 'transcript'
    assert payload['overall_score'] > 0
    assert len(payload['checks']) == 3
    assert payload['checks'][0]['status'] == 'pass'
    assert payload['checks'][0]['layer'] == 'caller_behavior'
    assert payload['checks'][0]['root_cause_tag'] == 'none'
    assert payload['vcon_analysis']['type'] == 'voice_ai_eval'
    assert payload['vcon_analysis']['body']['checks']
    assert payload['vcon_export']['analysis'][0]['type'] == 'voice_ai_eval'
    assert payload['vcon_export']['dialog'][0]['originator'] == 'Agent'
    assert payload['vcon_export']['dialog'][1]['originator'] == 'Caller'
    assert payload['vcon_export']['parties'] == [{'name': 'Agent'}, {'name': 'Caller'}]


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
