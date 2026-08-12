from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_assert_judge_endpoint_records_product_audit_metadata(monkeypatch):
    from app.routes import assert_sidecar

    run = {
        'execution_run_id': 'exec-assert-audit',
        'status': 'completed',
        'suite_id': 'call-center-voice-ai',
        'user_id': 'audit-user',
        'project_id': 'audit-project',
    }
    conversation = {
        'conversation_id': 'exec-assert-audit-refund-1',
        'execution_run_id': run['execution_run_id'],
        'suite_id': run['suite_id'],
        'scenario_id': 'refund-policy-boundary',
        'status': 'completed',
        'verdict': 'needs_review',
    }
    response_payload = {
        'status': 'ready',
        'required_plan': 'starter',
        'credits': 10,
        'provider': 'assert-ai',
        'model': 'openai/gpt-4.1-mini',
        'judge_output': '{"judge_status":"ok"}',
        'judge_result': {
            'agrees': False,
            'proposed_evaluation': {
                'verdict': 'needs_review',
                'summary': 'Execution remains unverified.',
                'corrected_findings': [],
                'remaining_gaps': ['No matching tool result.'],
            },
        },
    }

    monkeypatch.setattr(
        assert_sidecar.execution_run_store,
        'get_execution_run',
        lambda run_id: run,
    )
    monkeypatch.setattr(
        assert_sidecar.execution_run_store,
        'get_conversation',
        lambda run_id, conversation_id: conversation,
    )
    monkeypatch.setattr(
        assert_sidecar.execution_run_store,
        'deterministic_evaluation_snapshot',
        lambda value: {'verdict': value.get('verdict')},
    )
    monkeypatch.setattr(
        assert_sidecar.execution_run_store,
        'record_judge_review',
        lambda *args, **kwargs: {'review_id': 'judge-review-audit'},
    )
    monkeypatch.setattr(
        assert_sidecar,
        'get_scenario_contract',
        lambda suite_id, scenario_id: {'goal': 'Review safely.'},
    )
    monkeypatch.setattr(
        assert_sidecar,
        'run_upstream_assert_judge',
        lambda **kwargs: response_payload,
    )
    recorded = {}

    def fake_record_judge_request(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(assert_sidecar, 'record_judge_request', fake_record_judge_request)

    response = client.post(
        f"/api/assert/runs/{run['execution_run_id']}"
        f"/conversations/{conversation['conversation_id']}/judge",
        json={'user_id': run['user_id']},
    )

    assert response.status_code == 200
    assert response.json()['review_id'] == 'judge-review-audit'
    assert recorded['user_id'] == run['user_id']
    assert recorded['project_id'] == run['project_id']
    assert recorded['plan'] == 'starter'
    assert recorded['status'] == 'ready'
    assert recorded['credits'] == 10
    assert recorded['provider'] == 'assert-ai'
    assert recorded['model'] == 'openai/gpt-4.1-mini'
    assert recorded['judge_output'] == '{"judge_status":"ok"}'
    assert recorded['agrees'] is False
