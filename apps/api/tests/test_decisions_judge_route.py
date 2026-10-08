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
        snapshot = store.deterministic_evaluation_snapshot(kwargs['conversation'])
        request = judge.build_decisions_request(conversation=kwargs['conversation'], scenario_contract=kwargs['scenario_contract'])
        review['provenance'] = {'engine': 'openai_decisions', 'model': judge.MODEL, 'endpoint': judge.ENDPOINT,
                               'policy_version': judge.POLICY_VERSION,
                               'input_fingerprint': judge._input_fingerprint(request, snapshot, 0.9)}
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


@pytest.mark.parametrize('binding', ['revoked', 'ambiguous', 'missing_key'])
def test_invalid_project_binding_is_rejected_before_paid_judging(recorded, monkeypatch, binding):
    calls, audit = recorded
    run = store._RUNS['decisions-run']
    run['product_project_id'] = 'stable-project' if binding != 'ambiguous' else None
    if binding == 'missing_key': run['project_id'] = None
    def denied(**kwargs):
        if binding == 'missing_key': assert kwargs['project_id'] == ''
        raise ValueError('Project is not visible or is ambiguous.')
    monkeypatch.setattr(route, 'resolve_execution_product_project_id', denied)
    assert client.post(PATH, json={'user_id': 'owner'}).status_code == 409
    assert calls == [] and audit == []


def test_owner_unbound_run_does_not_require_a_product_project(recorded):
    store._RUNS['decisions-run']['project_id'] = None
    response = client.post(PATH, json={'user_id': 'owner'})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('kind', ['active_run', 'active_conversation', 'no_verdict', 'unknown_run', 'unknown_conversation'])
def test_endpoint_requires_terminal_deterministically_evaluated_calls(recorded, kind):
    calls, audit = recorded
    run = store._RUNS['decisions-run']
    if kind == 'active_run': run['status'] = 'running'
    if kind == 'active_conversation': run['conversations'][0]['status'] = 'running'
    if kind == 'no_verdict': run['conversations'][0]['verdict'] = None
    if kind == 'unknown_run': run['status'] = 'waiting'
    if kind == 'unknown_conversation': run['conversations'][0]['status'] = None
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


def test_changed_legacy_catalog_contract_cannot_record_stale_review(recorded, monkeypatch):
    store._RUNS['decisions-run']['conversations'][0]['evaluation_findings'] = {}
    contract = {'required_actions': ['cancel subscription']}
    monkeypatch.setattr(route, 'get_scenario_contract', lambda *args: deepcopy(contract))
    monkeypatch.setattr('app.services.benchmark_service.get_scenario_contract', lambda *args: deepcopy(contract))
    original_provider = route.run_openai_decisions_judge
    def provider(**kwargs):
        result = original_provider(**kwargs)
        contract['required_actions'] = ['verify identity first']
        return result
    monkeypatch.setattr(route, 'run_openai_decisions_judge', provider)
    response = client.post(PATH, json={'user_id': 'owner'})
    assert response.status_code == 409
    assert 'judging inputs changed' in response.json()['detail']
    assert not store.get_conversation('decisions-run', 'call')['judge_reviews']


@pytest.mark.parametrize('mutation', ['catalog', 'fingerprint', 'policy', 'threshold', 'model', 'not_terminal'])
def test_confirmation_rechecks_exact_decisions_inputs_without_mutation(recorded, monkeypatch, mutation):
    conv = store._RUNS['decisions-run']['conversations'][0]
    conv['evaluation_findings'] = {}
    contract = {'required_actions': ['cancel subscription']}
    monkeypatch.setattr(route, 'get_scenario_contract', lambda *args: deepcopy(contract))
    monkeypatch.setattr('app.services.benchmark_service.get_scenario_contract', lambda *args: deepcopy(contract))
    response = client.post(PATH, json={'user_id': 'owner'})
    assert response.status_code == 200, response.text
    review = conv['judge_reviews'][0]
    if mutation == 'catalog': contract['required_actions'] = ['verify identity first']
    if mutation == 'fingerprint': review['judge_result']['provenance']['input_fingerprint'] = 'invalid'
    if mutation == 'policy': review['judge_result']['policy']['version'] = 'unknown-version'
    if mutation == 'threshold': review['judge_result']['policy']['min_confidence'] = True
    if mutation == 'model': review['model'] = 'other-model'
    if mutation == 'not_terminal': conv['status'] = 'waiting'
    before = deepcopy(store._RUNS['decisions-run'])
    response = client.post('/api/execution/runs/decisions-run/conversations/call/judge-reviews/'
                           + review['review_id'] + '/apply', json={'user_id': 'owner', 'confirm': True})
    assert response.status_code == 409, response.text
    assert store._RUNS['decisions-run'] == before


def test_recorded_contract_and_threshold_stay_authoritative_on_confirmation(recorded, monkeypatch):
    response = client.post(PATH, json={'user_id': 'owner'})
    assert response.status_code == 200, response.text
    monkeypatch.setattr('app.services.benchmark_service.get_scenario_contract',
                        lambda *args: {'required_actions': ['new catalog policy']})
    monkeypatch.setenv('OPENAI_DECISIONS_JUDGE_MIN_CONFIDENCE', '0.99')
    review_id = response.json()['review_id']
    store.reset_execution_runs_for_tests()
    store.apply_judge_review('decisions-run', 'call', user_id='owner', review_id=review_id)
    applied = store.get_conversation('decisions-run', 'call')
    assert applied['evaluation_adjudication']['judge_result']['policy']['min_confidence'] == 0.9


@pytest.mark.parametrize('error,status', [(judge.DecisionsJudgeUnavailable, 503), (judge.DecisionsJudgeBusy, 429),
    (judge.DecisionsJudgeBudgetExceeded, 429), (judge.DecisionsJudgeFailed, 502), (ValueError, 422)])
def test_provider_failures_map_to_explicit_errors_without_persisting_success(recorded, monkeypatch, error, status):
    _, audit = recorded
    def provider(**kwargs): raise error('Unavailable')
    monkeypatch.setattr(route, 'run_openai_decisions_judge', provider)
    assert client.post(PATH, json={'user_id': 'owner'}).status_code == status
    assert audit == []
    assert not store.get_conversation('decisions-run', 'call')['judge_reviews']
