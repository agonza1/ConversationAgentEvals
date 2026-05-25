from fastapi.testclient import TestClient

from app.main import app
from app.services.product_service import reset_saved_runs_for_tests

client = TestClient(app)


def setup_function():
    reset_saved_runs_for_tests()


def test_product_config_exposes_pricing_auth_and_usage_gates():
    response = client.get('/api/product/config')

    assert response.status_code == 200
    payload = response.json()
    plans = {plan['id']: plan for plan in payload['pricing']}
    assert plans['starter']['price_label'] == '$19/month'
    assert plans['team']['price_label'] == '$99/month'
    assert plans['starter']['seats'] == 'Unlimited seats'
    assert plans['business']['price_label'] == 'Contact Us'
    assert payload['auth']['enabled'] is True
    assert payload['auth']['providers'] == ['email_link', 'google']
    assert {rule['id'] for rule in payload['usage_rules']} >= {'deterministic_eval', 'llm_judge', 'voice_webrtc_minute'}


def test_saved_runs_are_project_scoped_and_require_user_id():
    response = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {'run_id': 'abc', 'overall_score': 92},
            'transcript': 'Agent: verified and completed the update.',
        },
    )

    assert response.status_code == 200
    saved = response.json()
    assert saved['id']
    assert saved['project_id'] == 'call-center'

    list_response = client.get('/api/product/runs', params={'user_id': 'demo-user', 'project_id': 'call-center'})
    assert list_response.status_code == 200
    assert [run['id'] for run in list_response.json()] == [saved['id']]

    missing_user = client.get('/api/product/runs')
    assert missing_user.status_code == 422


def test_saved_run_export_returns_owner_scoped_json_payload():
    create_response = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {'run_id': 'abc', 'overall_score': 92},
            'transcript': 'Agent: verified and completed the update.',
        },
    )
    saved = create_response.json()

    export_response = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'demo-user'})

    assert export_response.status_code == 200
    exported = export_response.json()
    assert exported['id'] == saved['id']
    assert exported['filename'] == f"agentbench-call-center-{saved['id']}.json"
    assert exported['report']['overall_score'] == 92
    assert exported['transcript'] == 'Agent: verified and completed the update.'

    wrong_owner = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'other-user'})
    assert wrong_owner.status_code == 404


def test_llm_judge_is_gated_for_free_and_ready_for_paid_plans():
    free_response = client.post('/api/product/judge', json={'plan': 'free', 'report': {'overall_score': 82}})
    assert free_response.status_code == 200
    assert free_response.json()['status'] == 'blocked'
    assert free_response.json()['required_plan'] == 'starter'

    paid_response = client.post(
        '/api/product/judge',
        json={
            'plan': 'team',
            'report': {'evidence_spans': ['Verified customer identity', 'Created support ticket']},
            'transcript': 'Agent: verified customer identity.',
        },
    )
    assert paid_response.status_code == 200
    payload = paid_response.json()
    assert payload['status'] == 'ready'
    assert payload['credits'] == 10
    assert payload['evidence_citations'][:2] == ['Verified customer identity', 'Created support ticket']
