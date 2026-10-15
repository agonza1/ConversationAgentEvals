from __future__ import annotations

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import assisted_authoring as authoring
from app.services.editable_assert_spec import SpecGenerationFailed, SpecGenerationUnavailable

client = TestClient(app)


def content(scope='case'):
    return {
        'title': 'Latest requested time', 'role': 'appointment conversation agent',
        'objective': 'Confirm the latest requested time without inventing a completed booking.',
        'permissible_behavior': 'May clarify and repeat the requested time. Booking capability is not assumed.',
        'required_behaviors': [{'id': 'latest-time', 'label': 'Confirm the latest time', 'description': 'Repeat the final requested time.'}],
        'forbidden_behaviors': [{'id': 'claim-booking', 'label': 'Invent booking success', 'description': 'Do not claim an unconfirmed booking succeeded.'}],
        'scenarios': [{'id': f'time-{variant}', 'title': f'{variant} correction',
                       'steps': ['I meant afternoon, not morning.', 'Actually, please use 3 PM.'],
                       'expected_outcome': 'Confirm the latest request without claiming a booking.',
                       'behavior_id': 'latest-time', 'variant': variant}
                      for variant in (['normal'] if scope == 'case' else ['normal', 'boundary', 'adversarial'])],
        'questions': [], 'assumptions': ['This draft measures communication, not a confirmed booking.'],
        'evidence_requirements': ['transcript'],
    }


def generate(monkeypatch, data=None, **payload):
    calls = []
    def fake(prompt):
        calls.append(prompt)
        return json.dumps(data if data is not None else content(payload.get('scope', 'case'))), 'fake', 'small-test-model'
    monkeypatch.setattr(authoring, '_complete_generation', fake)
    response = client.post('/api/specs/assist', json={'request': 'Test a caller correcting their appointment time.', **payload})
    return response, calls


@pytest.mark.parametrize('scope,count', [('case', 1), ('set', 3)])
def test_one_request_drafts_without_saving_approving_or_running(monkeypatch, scope, count):
    response, calls = generate(monkeypatch, scope=scope)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(calls) == 1 and result['requires_user_approval'] is True
    assert result['provider'] == 'fake'
    spec = result['spec']
    assert spec['generated_content_status'] == 'draft' and spec['id'] is None
    assert len(spec['scenarios']) == count
    assert all(item['draft'] for item in [*spec['required_behaviors'], *spec['forbidden_behaviors'], *spec['scenarios']])
    assert not spec['runtime_overrides'] and not spec['extensions']
    assert 'untrusted source data' in calls[0] and 'missing evidence is unverified' in calls[0]


def test_critical_question_has_no_draft_or_assumed_permission(monkeypatch):
    response, _ = generate(monkeypatch, {'questions': [{'id': 'permission', 'question': 'Can this agent make bookings?', 'options': ['Collect requests only', 'Make bookings']}], 'title': 'Ignored draft'})
    assert response.status_code == 200
    assert response.json()['spec'] is None
    assert response.json()['questions'][0]['id'] == 'permission'


def test_answers_and_explicit_context_reach_generator_as_data(monkeypatch):
    response, calls = generate(monkeypatch, answers={'permission': 'Collect requests only'}, context={'label': 'Existing case', 'text': 'Never confirm an unverified booking.'})
    assert response.status_code == 200
    assert 'Collect requests only' in calls[0] and 'Existing case' in calls[0]
    assert 'Never confirm an unverified booking.' in response.json()['spec']['requirements']


def test_refinement_receives_user_edits_but_cannot_promote_old_draft_to_policy(monkeypatch):
    first, _ = generate(monkeypatch)
    spec = first.json()['spec']
    spec['required_behaviors'][0]['description'] = 'My reviewed edit'
    response, calls = generate(monkeypatch, current_spec=spec)
    assert response.status_code == 200 and 'My reviewed edit' in calls[0]
    assert spec['required_behaviors'][0]['description'] == 'My reviewed edit'
    invalid = content(); invalid['required_behaviors'][0]['source_quote'] = 'My reviewed edit'
    response, _ = generate(monkeypatch, invalid, current_spec=spec)
    assert response.status_code == 502


@pytest.mark.parametrize('mutate', [
    lambda data: data.update(runtime_overrides={'target_url': 'https://example.invalid'}),
    lambda data: data['scenarios'][0].update(behavior_id='unknown'),
    lambda data: data['scenarios'][0].update(steps=['   ']),
    lambda data: data['required_behaviors'][0].update(source_quote='invented source'),
    lambda data: data['forbidden_behaviors'][0].update(id='latest-time'),
    lambda data: data.update(scenarios=[]),
    lambda data: data.update(permissible_behavior=''),
    lambda data: data.update(evidence_requirements=['arbitrary_expression']),
])
def test_invalid_model_output_is_never_accepted(monkeypatch, mutate):
    data = deepcopy(content()); mutate(data)
    response, _ = generate(monkeypatch, data)
    assert response.status_code == 502 and 'existing work was not changed' in response.json()['detail']


def test_scope_counts_and_variants_are_enforced(monkeypatch):
    response, _ = generate(monkeypatch, content('set'), scope='case')
    assert response.status_code == 502
    data = content('set'); data['scenarios'][2]['variant'] = 'normal'
    response, _ = generate(monkeypatch, data, scope='set')
    assert response.status_code == 502


def test_exact_source_quotes_work_and_quick_case_can_have_no_forbidden_rule(monkeypatch):
    data = content(); data['required_behaviors'][0]['source_quote'] = 'caller correcting'
    data['forbidden_behaviors'] = []
    response, _ = generate(monkeypatch, data)
    assert response.status_code == 200


@pytest.mark.parametrize('payload', [
    {'request': 'short'}, {'request': 'x' * 8001}, {'scope': 'everything'},
    {'confirm': True}, {'context': {'label': 'x', 'text': 'x' * 16001}},
    {'answers': {'a': 'yes', 'b': 'yes', 'c': 'yes'}},
])
def test_invalid_requests_do_not_invoke_model(monkeypatch, payload):
    def never(_):
        raise AssertionError('No model call permitted')
    monkeypatch.setattr(authoring, '_complete_generation', never)
    response = client.post('/api/specs/assist', json={'request': 'A sufficiently detailed test request.', **payload})
    assert response.status_code == 422


@pytest.mark.parametrize('error,status', [(SpecGenerationUnavailable('No generation connection'), 503), (SpecGenerationFailed('Invalid provider result'), 502)])
def test_generator_failure_keeps_existing_http_error_contract(monkeypatch, error, status):
    def fail(_):
        raise error
    monkeypatch.setattr(authoring, '_complete_generation', fail)
    response = client.post('/api/specs/assist', json={'request': 'Test callers who change their minds.'})
    assert response.status_code == status


def test_clarification_cannot_turn_into_an_unbounded_model_loop(monkeypatch):
    data = {'questions': [{'id': 'permission', 'question': 'Can the agent book appointments?', 'options': []}]}
    response, calls = generate(monkeypatch, data, answers={'permission': 'Collect requests only'})
    assert response.status_code == 502 and len(calls) == 1


def test_overlapping_cancel_retry_is_not_queued_or_sent_to_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(authoring, '_complete_generation', lambda _: calls.append(True))
    assert authoring._GENERATION_SLOT.acquire(blocking=False)
    try:
        response = client.post('/api/specs/assist', json={'request': 'Test caller corrections.'})
        assert response.status_code == 429 and response.headers['retry-after'] == '5'
        assert not calls
    finally:
        authoring._GENERATION_SLOT.release()


def test_quick_case_retains_reviewed_evidence_and_policy_across_reload(tmp_path, monkeypatch):
    from app.services import user_scenario_store, benchmark_service
    monkeypatch.setattr(user_scenario_store, '_STORE_PATH', tmp_path / 'user.json')
    user_scenario_store.configure_store_path(tmp_path / 'user.json')
    try:
        response = client.post('/api/scenarios', json={
            'title': 'Reviewed assisted case', 'simulated_user_prompt': 'Use the corrected time.',
            'expected_output': 'Confirm the corrected request.', 'description': 'Follow the latest request.',
            'required_actions': ['Repeat the latest time'], 'forbidden_actions': ['Invent booking success'],
            'evidence_requirements': ['transcript', 'final_state_or_action_trace'],
            'requirements': 'No booking claims without evidence.', 'permissible_behavior': 'May collect requests only.',
            'caller_steps': ['Use 3 PM, not morning.', 'Confirm afternoon.'],
        })
        assert response.status_code == 200, response.text
        record = response.json()
        user_scenario_store.configure_store_path(tmp_path / 'user.json')
        retained = client.get(f'/api/scenarios/{record["id"]}').json()
        contract = benchmark_service.get_scenario_contract('user-scenarios', record['id'])['scenario_contract']
        for item in (record, retained, contract):
            assert item['evidence_requirements'] == ['transcript', 'final_state_or_action_trace']
            assert item['requirements'] == 'No booking claims without evidence.'
            assert item['permissible_behavior'] == 'May collect requests only.'
            assert item['caller_steps'] == ['Use 3 PM, not morning.', 'Confirm afternoon.']
        from app.services.execution_runner import _scenario_user_opener
        assert _scenario_user_opener(contract) == 'Use 3 PM, not morning.'
        assert benchmark_service._simulated_user_opener(contract) == 'Use 3 PM, not morning.'
        from app.services.design_enforcement import evaluate_design
        checks = evaluate_design(contract, {}, 'The requested time is 3 PM.')
        assert any(check['status'] == 'insufficient_evidence' for check in checks['evidence_requirement_results'])
    finally:
        user_scenario_store.reset_user_scenarios_for_tests()
        user_scenario_store.configure_store_path(None)
