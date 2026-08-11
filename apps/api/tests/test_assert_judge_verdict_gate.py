import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import assert_sidecar


client = TestClient(app)


def test_assert_judge_rejects_failed_conversation_without_deterministic_verdict(
    monkeypatch,
):
    run = {
        'execution_run_id': 'exec-failed-before-evaluation',
        'status': 'failed',
        'suite_id': 'call-center-voice-ai',
        'user_id': 'owner',
    }
    conversation = {
        'conversation_id': 'exec-failed-before-evaluation-refund-1',
        'execution_run_id': run['execution_run_id'],
        'suite_id': run['suite_id'],
        'scenario_id': 'refund-policy-boundary',
        'status': 'failed',
        'verdict': None,
        'error': 'Target execution failed before deterministic evaluation.',
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
        assert_sidecar,
        'run_upstream_assert_judge',
        lambda **kwargs: pytest.fail('ASSERT must not run without a deterministic verdict.'),
    )

    response = client.post(
        (
            f"/api/assert/runs/{run['execution_run_id']}"
            f"/conversations/{conversation['conversation_id']}/judge"
        ),
        json={'user_id': run['user_id']},
    )

    assert response.status_code == 409
    assert response.json()['detail'] == (
        'The conversation must have a deterministic verdict before ASSERT judging.'
    )
