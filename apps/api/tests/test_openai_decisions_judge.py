from copy import deepcopy
import json

import httpx
import pytest

from app.services import openai_decisions_judge as judge


def conversation(verdict='pass'):
    return {
        'conversation_id': 'call-1', 'scenario_id': 'cancel', 'status': 'completed',
        'verdict': verdict,
        'turns': [{'turn_index': 1, 'speaker': 'agent', 'text': 'Your subscription is canceled.'}],
        'final_state': {'subscription': {'status': 'active'}},
    }


def request():
    return judge.build_decisions_request(conversation=conversation(), scenario_contract=None)


def response_for(req, choice='no_violation', confidence=0.95):
    return {
        'model': judge.MODEL,
        'usage': {'input_tokens': 200, 'output_tokens': 0, 'total_tokens': 200},
        'answers': [{
            'type': 'choice', 'name': question['name'], 'choice': choice, 'confidence': confidence,
            'probabilities': [{'value': option, 'probability': confidence if option == choice else (1-confidence)/2} for option in judge.CHOICES],
        } for question in req['questions']],
    }


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv('OPENAI_DECISIONS_JUDGE_ENABLED', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-secret')
    monkeypatch.delenv('OPENAI_DECISIONS_JUDGE_MIN_CONFIDENCE', raising=False)
    monkeypatch.setattr(judge, '_judge_spend_control', lambda: {})
    monkeypatch.setattr(judge, '_reserve_judge_credits', lambda *args, **kwargs: (True, {}))
    refunded = []
    monkeypatch.setattr(judge, '_refund_judge_credits', lambda *args, **kwargs: refunded.append(kwargs['credits']))
    calls = []

    def post(client, url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json=response_for(kwargs['json']), headers={'x-request-id': 'req-unit'})

    monkeypatch.setattr(httpx.Client, 'post', post)
    return calls, refunded


def test_builds_bounded_questions_from_recorded_contract_without_sending_old_verdict():
    item = conversation()
    item['evaluation_findings'] = {'scenario_contract': {'required_actions': ['verify identity'], 'forbidden_actions': ['guarantee refund']}}
    req = judge.build_decisions_request(conversation=item, scenario_contract={'required_actions': ['stale catalog action']})
    encoded = json.dumps(req)
    assert 'verify identity' in encoded and 'guarantee refund' in encoded
    assert 'stale catalog' not in encoded
    assert 'verdict' not in json.loads(req['input'])
    assert 'not proof an operation executed' in encoded
    assert len({q['name'] for q in req['questions']}) == len(req['questions'])
    assert all(q['type'] == 'choice' for q in req['questions'])


def test_empty_and_oversized_evidence_are_rejected_without_truncation():
    with pytest.raises(ValueError, match='no recorded evidence'):
        judge.build_decisions_request(conversation={}, scenario_contract=None)
    item = conversation()
    item['transcript'] = 'x' * 256_000
    with pytest.raises(ValueError, match='not truncated'):
        judge.build_decisions_request(conversation=item, scenario_contract=None)
    with pytest.raises(ValueError, match='no rules were dropped'):
        judge.build_decisions_request(conversation=conversation(), scenario_contract={'required_actions': [f'rule {i}' for i in range(33)]})


@pytest.mark.parametrize('original', ['fail', 'failed', 'needs_review'])
def test_confident_no_violation_never_upgrades_a_deterministic_failure_or_gap(original):
    result = judge._review(judge.validate_decisions_response(response_for(request(), confidence=1), request(), 0.9), original, 0.9)
    assert result['proposed_evaluation']['verdict'] == ('fail' if original in {'fail', 'failed'} else 'needs_review')


@pytest.mark.parametrize('choice,confidence', [('no_violation', 0.6), ('violation', 0.6), ('insufficient_evidence', 0.99)])
def test_missing_evidence_and_low_confidence_go_to_review(choice, confidence):
    req = request()
    decisions = judge.validate_decisions_response(response_for(req, choice, confidence), req, 0.9)
    assert all(d['status'] == 'insufficient_evidence' for d in decisions)
    assert judge._review(decisions, 'pass', 0.9)['proposed_evaluation']['verdict'] == 'needs_review'


def test_confident_violation_can_propose_failure():
    req = request()
    decisions = judge.validate_decisions_response(response_for(req, 'violation'), req, 0.9)
    assert judge._review(decisions, 'pass', 0.9)['proposed_evaluation']['verdict'] == 'fail'


def test_per_question_refusal_is_retained_as_insufficient_evidence():
    req = request()
    result = response_for(req)
    result['answers'][0] = {'name': req['questions'][0]['name'], 'type': 'refusal'}
    decisions = judge.validate_decisions_response(result, req, 0.9)
    assert decisions[0]['reason'] == 'provider_refusal'
    assert decisions[1]['status'] == 'no_violation'
    assert judge._review(decisions, 'pass', 0.9)['proposed_evaluation']['verdict'] == 'needs_review'


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'order', 'choice', 'nan', 'bool', 'distribution', 'confidence', 'model', 'usage'])
def test_malformed_provider_outputs_never_become_ready_reviews(mutation):
    req = request()
    result = response_for(req)
    if mutation == 'missing': result['answers'].pop()
    if mutation == 'duplicate': result['answers'][1]['name'] = result['answers'][0]['name']
    if mutation == 'order': result['answers'].reverse()
    if mutation == 'choice': result['answers'][0]['choice'] = 'invented'
    if mutation == 'nan': result['answers'][0]['confidence'] = float('nan')
    if mutation == 'bool': result['answers'][0]['confidence'] = True
    if mutation == 'distribution': result['answers'][0]['probabilities'][0]['probability'] = 1
    if mutation == 'confidence': result['answers'][0]['confidence'] = -0.1
    if mutation == 'model': result['model'] = 'unexpected'
    if mutation == 'usage': result['usage']['input_tokens'] = True
    with pytest.raises(judge.DecisionsJudgeFailed):
        judge.validate_decisions_response(result, req, 0.9)


def test_confidence_is_separate_from_selected_probability_as_in_official_example():
    req = request()
    result = response_for(req, confidence=0.95)
    result['answers'][0]['confidence'] = 0.93
    assert judge.validate_decisions_response(result, req, 0.9)[0]['status'] == 'no_violation'
    result['answers'][0]['confidence'] = 0.85
    assert judge.validate_decisions_response(result, req, 0.9)[0]['status'] == 'insufficient_evidence'
    result = response_for(req, confidence=0.6)
    result['answers'][0]['confidence'] = 0.99
    assert judge.validate_decisions_response(result, req, 0.9)[0]['status'] == 'insufficient_evidence'


def test_real_service_uses_documented_endpoint_and_preserves_inputs_and_provenance(provider):
    calls, refunded = provider
    item = conversation()
    before = deepcopy(item)
    result = judge.run_openai_decisions_judge(run={'execution_run_id': 'run-1', 'status': 'completed'}, conversation=item)
    assert item == before
    assert calls[0][0] == 'https://api.openai.com/v1/decisions'
    assert calls[0][1]['headers'] == {'Authorization': 'Bearer test-secret'}
    assert result['judge_result']['provenance']['request_id'] == 'req-unit'
    assert result['judge_result']['provenance']['deterministic_snapshot']['verdict'] == 'pass'
    assert len(result['judge_result']['provenance']['input_fingerprint']) == 64
    assert 'test-secret' not in json.dumps(result)
    assert result['evidence_citations'] == []
    assert refunded == []


def test_disabled_and_missing_credentials_do_not_issue_provider_requests(provider, monkeypatch):
    calls, _ = provider
    monkeypatch.setenv('OPENAI_DECISIONS_JUDGE_ENABLED', '0')
    with pytest.raises(judge.DecisionsJudgeUnavailable):
        judge.run_openai_decisions_judge(run={'status': 'completed'}, conversation=conversation())
    monkeypatch.setenv('OPENAI_DECISIONS_JUDGE_ENABLED', '1')
    monkeypatch.delenv('OPENAI_API_KEY')
    monkeypatch.delenv('LLM_JUDGE_API_KEY', raising=False)
    with pytest.raises(judge.DecisionsJudgeUnavailable):
        judge.run_openai_decisions_judge(run={'status': 'completed'}, conversation=conversation())
    assert calls == []


@pytest.mark.parametrize('source', ['run', 'conversation'])
def test_service_rejects_unknown_states_without_provider_calls(provider, source):
    calls, refunded = provider
    run, item = {'status': 'completed'}, conversation()
    (run if source == 'run' else item)['status'] = 'waiting'
    with pytest.raises(ValueError, match='terminal'):
        judge.run_openai_decisions_judge(run=run, conversation=item)
    assert calls == [] and refunded == []


@pytest.mark.parametrize('failure', ['http', 'timeout', 'invalid_json', 'invalid_answer'])
def test_failure_refunds_credits_and_hides_provider_content(provider, monkeypatch, failure):
    _, refunded = provider
    def post(*args, **kwargs):
        if failure == 'timeout': raise httpx.ReadTimeout('test-secret private transcript')
        if failure == 'http': return httpx.Response(403, text='test-secret private transcript')
        if failure == 'invalid_json': return httpx.Response(200, text='private transcript')
        return httpx.Response(200, json={'model': judge.MODEL, 'answers': []})
    monkeypatch.setattr(httpx.Client, 'post', post)
    with pytest.raises(judge.DecisionsJudgeFailed) as exc:
        judge.run_openai_decisions_judge(run={'status': 'completed'}, conversation=conversation())
    assert 'test-secret' not in str(exc.value) and 'private transcript' not in str(exc.value)
    assert refunded == [10]


def test_budget_and_concurrency_admission_precede_network(provider, monkeypatch):
    calls, refunded = provider
    monkeypatch.setattr(judge, '_reserve_judge_credits', lambda *args, **kwargs: (False, {}))
    with pytest.raises(judge.DecisionsJudgeBudgetExceeded):
        judge.run_openai_decisions_judge(run={'status': 'completed'}, conversation=conversation())
    assert calls == [] and refunded == []
    with judge._slot(), judge._slot():
        with pytest.raises(judge.DecisionsJudgeBusy):
            judge.run_openai_decisions_judge(run={'status': 'completed'}, conversation=conversation())
