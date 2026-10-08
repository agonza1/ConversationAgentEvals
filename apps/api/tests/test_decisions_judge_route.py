from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import decisions_judge as route
from app.schemas.execution import ConversationRecord, ExecutionRunRecord, ExecutionRunProgress
from app.services import execution_run_store as store
from app.services import openai_decisions_judge as judge

client = TestClient(app)
PATH = '/api/decisions/runs/decisions-run/conversations/call/judge'


@pytest.fixture
def recorded(monkeypatch, tmp_path):
    store.reset_execution_runs_for_tests()
    monkeypatch.setattr(store, 'RUNS_DIR', tmp_path / 'artifacts' / 'execution-runs')
    monkeypatch.setattr(store, 'REPO_ROOT', tmp_path)
    conversation = ConversationRecord(conversation_id='call', execution_run_id='decisions-run',
        suite_id='suite', scenario_id='case', mode='text_callable', status='completed',
        transcript='Agent: Canceled.', verdict='fail', score=20,
        final_state={'subscription': {'status': 'active'}},
        evaluation_findings={'scenario_contract': {'required_actions': ['cancel subscription']}})
    store.create_execution_run(ExecutionRunRecord(execution_run_id='decisions-run', status='needs_review',
        mode='text_callable', suite_id='suite', scenario_ids=['case'], user_id='owner', project_id='project',
        progress=ExecutionRunProgress(phase='completed', completed_conversations=1, total_conversations=1, percent=100),
        conversations=[conversation], created_at='2026-10-08T00:00:00Z', updated_at='2026-10-08T00:00:00Z'))
    calls, audit = [], []
    monkeypatch.setattr(route, 'get_scenario_contract', lambda *args: None)
    monkeypatch.setattr(route, 'resolve_execution_product_project_id', lambda **kwargs: None)
    monkeypatch.setattr(route, 'record_judge_request', lambda **kwargs: audit.append(kwargs))

    def provider(**kwargs):
        calls.append(deepcopy(kwargs))
        decisions = [{'name': 'unsupported_operational_claim', 'status': 'no_violation', 'answer': {'type': 'choice', 'choice': 'no_violation', 'confidence': 1}}]
        review = judge._review(decisions, kwargs['conversation']['verdict'], 0.9)
        review['provenance'] = {'engine': 'openai_decisions', 'model': judge.MODEL, 'input_fingerprint': 'fingerprint'}
        return {'status': 'ready', 'provider': 'openai', 'model': judge.MODEL, 'credits': 10,
                'judge_output': '{"answers":[]}', 'judge_result': review}

    monkeypatch.setattr(route, 'run_openai_decisions_judge', provider)
    yield calls, audit
    store.reset_execution_runs_for_tests()


def test_endpoint_preserves_deterministic_evidence_through_pending_apply_and_reload(recorded):
    calls, audit = recorded
    before = store.get_conversation('decisions-run', 'call')
    response = client.post(PATH, json={'user_id': 'owner'})
    assert response.status_code == 200, response.text
    review_id = response.json()['review_id']
    pending = store.get_conversation('decisions-run', 'call')
    assert pending['evaluation_adjudication'] is None
    assert pending['judge_reviews'][0]['status'] == 'pending_confirmation'
    assert pending['judge_reviews'][0]['judge_result']['decisions'][0]['answer']['confidence'] == 1
    assert pending['judge_reviews'][0]['judge_result']['proposed_evaluation']['verdict'] == 'fail'
    assert calls[0]['conversation']['transcript'] == before['transcript']
    assert audit[0]['provider'] == 'openai' and audit[0]['model'] == judge.MODEL
    store.apply_judge_review('decisions-run', 'call', user_id='owner', review_id=review_id)
    store.reset_execution_runs_for_tests()
    applied = store.get_conversation('decisions-run', 'call')
    for key in ('transcript', 'final_state', 'evaluation_findings', 'verdict', 'score'):
        assert applied[key] == before[key]
    assert applied['evaluation_adjudication']['judge_result']['provenance']['engine'] == 'openai_decisions'


def test_endpoint_rejects_owner_mismatch_and_client_injected_evidence(recorded):
    calls, audit = recorded
    assert client.post(PATH, json={'user_id': 'other'}).status_code == 404
    assert client.post(PATH, json={'user_id': 'owner', 'transcript': 'fabricated'}).status_code == 422
    assert client.post(PATH, json={'user_id': 'owner', 'model_name': 'other'}).status_code == 422
    assert calls == [] and audit == []


@pytest.mark.parametrize('kind', ['active_run', 'active_conversation', 'no_verdict'])
def test_endpoint_requires_terminal_deterministically_evaluated_calls(recorded, kind):
    calls, audit = recorded
    run = store._RUNS['decisions-run']
    if kind == 'active_run': run['status'] = 'running'
    if kind == 'active_conversation': run['conversations'][0]['status'] = 'running'
    if kind == 'no_verdict': run['conversations'][0]['verdict'] = None
    assert client.post(PATH, json={'user_id': 'owner'}).status_code == 409
    assert calls == [] and audit == []


def test_changed_evidence_cannot_record_a_stale_review(recorded, monkeypatch):
    original_provider = route.run_openai_decisions_judge
    def provider(**kwargs):
        result = original_provider(**kwargs)
        store._RUNS['decisions-run']['conversations'][0]['transcript'] = 'Changed evidence.'
        return result
    monkeypatch.setattr(route, 'run_openai_decisions_judge', provider)
    response = client.post(PATH, json={'user_id': 'owner'})
    assert response.status_code == 409
    assert 'changed' in response.json()['detail']
    assert not store.get_conversation('decisions-run', 'call')['judge_reviews']


@pytest.mark.parametrize('error,status', [(judge.DecisionsJudgeUnavailable, 503), (judge.DecisionsJudgeBusy, 429),
    (judge.DecisionsJudgeBudgetExceeded, 429), (judge.DecisionsJudgeFailed, 502), (ValueError, 422)])
def test_provider_failures_map_to_explicit_errors_without_persisting_success(recorded, monkeypatch, error, status):
    _, audit = recorded
    def provider(**kwargs): raise error('Unavailable')
    monkeypatch.setattr(route, 'run_openai_decisions_judge', provider)
    assert client.post(PATH, json={'user_id': 'owner'}).status_code == status
    assert audit == []
    assert not store.get_conversation('decisions-run', 'call')['judge_reviews']
