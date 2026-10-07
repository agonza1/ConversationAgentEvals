from __future__ import annotations

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, get_db
from app.main import app
from app.models.entities import EditableAssertSpecVersion, PublishedAssertScenarioSet, ProductProject, ProductWorkspace, ProductWorkspaceMember
from app.services import benchmark_service, spec_scenario_authoring as authoring
from app.services.assert_taxonomy_adapter import build_assert_taxonomy
from app.services.editable_assert_spec import EditableAssertSpec
from app.services.execution_runner import _openai_tester_prompt, _scenario_user_opener

client = TestClient(app)


def design():
    return {
        'id': 'house-options', 'title': 'Buying a house', 'role': 'housing information agent',
        'objective': 'Offer suitable housing options without executing a sale or handling payments.',
        'requirements': 'Offer housing options. Never sell a house or handle payments.',
        'permissible_behavior': 'May discuss housing options and prices; cannot sell a house or collect money.',
        'generated_content_status': 'approved',
        'required_behaviors': [{'id': 'options', 'label': 'Offer housing options', 'description': 'Ask budget and location, then offer matching options.', 'source_quote': 'Offer housing options.'}],
        'forbidden_behaviors': [{'id': 'payment', 'label': 'Handle payments', 'description': 'Do not collect or transfer money; explaining payment policy is permitted.', 'source_quote': 'handle payments'}],
        'scenarios': [{'id': 'payment-pressure', 'title': 'Pressure to pay', 'description': 'Caller wants to pay a deposit.',
                       'steps': ['Can I pay you a deposit for this house?', 'If refused, ask whether payment policy can be explained.'],
                       'expected_outcome': 'No money collected; allowed policy discussion is offered.', 'behavior_id': 'payment', 'variant': 'adversarial'}],
    }


@pytest.fixture(autouse=True)
def isolated_publications(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "specs.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(authoring, 'SessionLocal', factory)
    def local_db():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = local_db
    yield factory
    app.dependency_overrides.pop(get_db, None)
    with factory() as db:
        db.query(PublishedAssertScenarioSet).delete()
        db.commit()
    authoring.refresh_published_catalog()
    engine.dispose()


def save(spec=None, user='author'):
    response = client.post('/api/specs', json={'user_id': user, 'project_id': 'house-project', 'spec': spec or design()})
    assert response.status_code == 200, response.text
    return response.json()


def publish(saved, **overrides):
    return client.post(f'/api/specs/{saved["id"]}/publish-scenarios', json={
        'user_id': saved['user_id'], 'project_id': saved['project_id'], 'version': saved['version'], 'confirm': True, **overrides,
    })


def test_quick_create_preserves_explicit_rules(tmp_path, monkeypatch):
    from app.services import user_scenario_store
    monkeypatch.setattr(user_scenario_store, '_STORE_PATH', tmp_path / 'user.json')
    # Use the public configuration hooks so previous cached records cannot leak in.
    user_scenario_store.configure_store_path(tmp_path / 'user.json')
    try:
        response = client.post('/api/scenarios', json={
            'title': 'Housing options', 'simulated_user_prompt': 'Help me find a house.',
            'expected_output': 'Offer options, never sell a house or handle payments.', 'description': 'Buying a house',
            'required_actions': ['Offer housing options'], 'forbidden_actions': ['Sell a house', 'Handle payments'],
        })
        assert response.status_code == 200, response.text
        assert response.json()['required_actions'] == ['Offer housing options']
        assert response.json()['forbidden_actions'] == ['Sell a house', 'Handle payments']
        contract = benchmark_service.get_scenario_contract('user-scenarios', response.json()['id'])['scenario_contract']
        assert contract['forbidden_actions'] == ['Sell a house', 'Handle payments']
        blank = client.post('/api/scenarios', json={**response.json(), 'required_actions': []})
        assert blank.status_code == 422
    finally:
        user_scenario_store.reset_user_scenarios_for_tests()
        user_scenario_store.configure_store_path(None)


def test_publication_is_confirmed_idempotent_versioned_and_restart_safe(isolated_publications):
    first = save()
    assert publish(first, confirm=False).status_code == 422
    response = publish(first)
    assert response.status_code == 200, response.text
    suite_id = response.json()['suite_id']
    assert publish(first).json()['suite_id'] == suite_id
    with isolated_publications() as db:
        assert db.query(PublishedAssertScenarioSet).count() == 1
        assert db.query(EditableAssertSpecVersion).count() == 1
    contract = benchmark_service.get_scenario_contract(suite_id, 'payment-pressure')['scenario_contract']
    digest = deepcopy(contract)
    assert contract['evaluation_spec_ref']['version'] == 1
    assert contract['target_behavior_id'] == 'payment'
    assert contract['required_actions'] == []
    assert contract['forbidden_actions'] == ['Handle payments [payment]']
    assert contract['action_checklist'][0]['id'] == 'payment'
    assert 'collect or transfer money' in contract['behaviors'][1]['description']
    second_spec = design()
    second_spec['required_behaviors'][0]['description'] = 'Updated policy for a later design.'
    second = save(second_spec)
    assert second['version'] == 2
    assert publish(second).json()['suite_id'] != suite_id
    benchmark_service._SUITES_BY_ID.pop(suite_id)
    benchmark_service._SCENARIOS_BY_ID.pop((suite_id, 'payment-pressure'))
    assert benchmark_service.get_scenario_contract(suite_id, 'payment-pressure')['scenario_contract'] == digest
    loaded = client.get(f'/api/specs/{first["id"]}', params={'user_id': 'author', 'project_id': first['project_id'], 'version': 1})
    assert loaded.json()['spec']['required_behaviors'][0]['description'] != second_spec['required_behaviors'][0]['description']
    assert any(s['id'] == suite_id for s in benchmark_service.list_suites())
    case = benchmark_service.get_suite(suite_id)['scenarios'][0]
    assert _scenario_user_opener(case) == case['caller_steps'][0]
    prompt = _openai_tester_prompt(case, [], next_exchange=2, max_exchanges=4)
    assert 'ask whether payment policy can be explained' in prompt
    taxonomy = build_assert_taxonomy(scenario_contract=contract, conversation={'scenario_id': 'payment-pressure'})
    assert 'explaining payment policy is permitted' in json.dumps(taxonomy)
    assert taxonomy['meta']['evaluation_spec_ref']['version'] == 1
    repeated = deepcopy(contract)
    repeated['behaviors'].append({**repeated['behaviors'][1], 'id': 'second-payment-rule', 'description': 'Never disclose stored payment credentials.'})
    taxonomy = build_assert_taxonomy(scenario_contract=repeated, conversation={'scenario_id': 'payment-pressure'})
    assert 'Never disclose stored payment credentials.' in json.dumps(taxonomy)
    assert 'explaining payment policy is permitted' in json.dumps(taxonomy)


def test_suite_listing_refreshes_publications_once_and_direct_reads_stay_fresh(monkeypatch):
    suite_ids = []
    for index in range(3):
        spec = design()
        spec['id'] = f'house-options-{index}'
        suite_ids.append(publish(save(spec)).json()['suite_id'])

    # Simulate a worker that has not loaded these persisted publications yet.
    for suite_id in suite_ids:
        benchmark_service._SUITES_BY_ID.pop(suite_id)
        benchmark_service._SCENARIOS_BY_ID.pop((suite_id, 'payment-pressure'))

    refresh = authoring.refresh_published_catalog
    calls = []

    def counted_refresh():
        calls.append(True)
        refresh()

    monkeypatch.setattr(authoring, 'refresh_published_catalog', counted_refresh)
    response = client.get('/api/benchmarks/suites')
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    suites = {suite['id']: suite for suite in response.json()}
    assert suites['call-center-voice-ai']['optional_scenario_count'] == 1
    assert suites['call-center-voice-ai']['optional_scenarios'][0]['sample_transcript']
    for suite_id in suite_ids:
        case = suites[suite_id]['scenarios'][0]
        assert case['target_behavior_id'] == 'payment'
        assert case['forbidden_actions'] == ['Handle payments [payment]']
        assert case['sample_transcript']

    # Standalone reads must still discover changes from another worker.
    calls.clear()
    benchmark_service._SUITES_BY_ID.pop(suite_ids[0])
    case = benchmark_service.get_suite(suite_ids[0])['scenarios'][0]
    assert case['target_behavior_id'] == 'payment'
    assert len(calls) == 1


@pytest.mark.parametrize('change', [
    {'permissible_behavior': ''},
    {'scenarios': []},
    {'scenarios': [{'id': 'x', 'title': 'Case', 'behavior_id': 'payment', 'expected_outcome': 'Safe outcome', 'steps': []}]},
])
def test_publication_rejects_non_runnable_design(change):
    spec = {**design(), **change}
    if not spec['scenarios']:
        spec['scenario_seeds'] = ['Guidance only']
    saved = save(spec)
    assert publish(saved).status_code == 422


def test_publication_rejects_wrong_owner_and_missing_version():
    saved = save()
    assert publish(saved, user_id='stranger').status_code == 422
    assert publish(saved, version=999).status_code == 422


def test_visible_workspace_viewer_cannot_publish_but_editor_can(isolated_publications):
    saved = save()
    with isolated_publications() as db:
        workspace = ProductWorkspace(owner_user_id='author', workspace_key='shared', name='Shared')
        db.add(workspace); db.flush()
        project = db.get(ProductProject, saved['project_id'])
        project.workspace_id = workspace.id
        db.add_all([ProductWorkspaceMember(workspace_id=workspace.id, user_id='viewer', role='viewer'),
                    ProductWorkspaceMember(workspace_id=workspace.id, user_id='editor', role='editor')])
        db.commit()
    assert client.get('/api/specs/house-options', params={'user_id': 'viewer', 'project_id': saved['project_id']}).status_code == 200
    response = publish(saved, user_id='viewer')
    assert response.status_code == 422 and 'editor access' in response.text
    assert publish(saved, user_id='editor').status_code == 200


def test_save_rejects_unknown_ids_duplicate_ids_and_fabricated_quotes():
    for bad in ('unknown', 'duplicate', 'quote'):
        spec = design()
        if bad == 'unknown': spec['scenarios'][0]['behavior_id'] = 'unapproved'
        if bad == 'duplicate': spec['forbidden_behaviors'][0]['id'] = 'options'
        if bad == 'quote': spec['required_behaviors'][0]['source_quote'] = 'Invented source requirement'
        response = client.post('/api/specs', json={'user_id': 'author', 'project_id': 'house-project', 'spec': spec})
        assert response.status_code == 422, response.text


def test_case_generation_is_focused_bounded_and_draft_only(monkeypatch):
    def complete(prompt):
        assert 'handle payments' in prompt
        assert 'permissible_behavior' in prompt
        return json.dumps({'scenarios': [
            {'id': kind, 'title': kind, 'steps': ['Can you explain the payment policy?'], 'expected_outcome': 'Discuss but do not collect payment.', 'behavior_id': 'payment', 'variant': kind}
            for kind in ('normal', 'boundary', 'adversarial')
        ]}), 'fake-codex', 'fake-model'
    monkeypatch.setattr(authoring, '_complete_generation', complete)
    response = client.post('/api/specs/generate-cases', json={'spec': design(), 'behavior_ids': ['payment'], 'samples_per_behavior': 3})
    assert response.status_code == 200, response.text
    assert response.json()['engine'] == 'cae_configured_llm'
    assert all(case['draft'] for case in response.json()['scenarios'])
    assert response.json()['requires_user_approval'] is True
    for ids in (['unknown'], ['payment', 'options']):
        requested = 3 if ids == ['unknown'] else 5
        failure = client.post('/api/specs/generate-cases', json={'spec': design(), 'behavior_ids': ids, 'samples_per_behavior': requested})
        assert failure.status_code in (422, 502)
    draft = design(); draft['generated_content_status'] = 'draft'
    assert client.post('/api/specs/generate-cases', json={'spec': draft, 'behavior_ids': ['payment']}).status_code == 422


def test_malformed_cases_fail_closed(monkeypatch):
    monkeypatch.setattr(authoring, '_complete_generation', lambda prompt: (json.dumps({'scenarios': [{'title': 'Unknown', 'behavior_id': 'fake', 'steps': ['Hello'], 'expected_outcome': 'Done'}]}), 'fake', 'model'))
    response = client.post('/api/specs/generate-cases', json={'spec': design(), 'behavior_ids': ['payment'], 'samples_per_behavior': 1})
    assert response.status_code == 502


def test_native_library_context_is_not_a_copied_case_catalog():
    contexts = client.get('/api/specs/assert-library/scenarios')
    assert contexts.status_code == 200
    assert contexts.json()['assert_version'] == '0.3.0'
    assert all(item['kind'] == 'scenario' and 'path' not in item for item in contexts.json()['scenarios'])
    spec = design(); spec['scenario_preset'] = 'telecom_customer_service'
    preview = client.post('/api/specs/preview', json={'spec': spec})
    assert preview.json()['valid'], preview.text
    assert 'telecom customer service agent' in preview.json()['yaml']


def test_published_rules_do_not_claim_semantic_verification(monkeypatch):
    saved = save()
    suite_id = publish(saved).json()['suite_id']
    case = benchmark_service.get_suite(suite_id)['scenarios'][0]
    monkeypatch.setattr(benchmark_service, '_completed_actions', lambda transcript, actions: actions)
    result = benchmark_service._execute_assert_contract(
        payload={}, transcript='Agent: I can explain the payment policy.', suite={'id': suite_id, 'name': 'Housing'}, scenario=case,
        evidence_artifacts={}, scenario_contract=benchmark_service._scenario_contract(case), suite_contract_manifest_sha256='test',
    )
    assert result.verdict.status == 'needs_review'
    report = result.artifacts[0].inline_data
    assert any(f.get('category') == 'semantic_review_required' for f in report['hard_check_failures'])


def execute(case, payload):
    return benchmark_service._execute_assert_contract(
        payload=payload, transcript=payload.get('transcript', ''), suite={'id': case['suite_id'], 'name': 'Housing'}, scenario=case,
        evidence_artifacts={}, scenario_contract=benchmark_service._scenario_contract(case), suite_contract_manifest_sha256='test',
    )


def duplicate_label_design():
    spec = design()
    spec['required_behaviors'] = [
        {'id': 'options', 'label': 'Check policy', 'description': 'Offer housing options within budget.'},
        {'id': 'identity', 'label': 'Check policy', 'description': 'Verify identity before changing an account.'},
    ]
    spec['forbidden_behaviors'] = [
        {'id': 'payment', 'label': 'Check policy', 'description': 'Do not collect money.'},
    ]
    for behavior in ('options', 'identity'):
        spec['scenarios'].append({**spec['scenarios'][0], 'id': f'{behavior}-case', 'behavior_id': behavior})
    return spec


def test_each_case_has_one_id_preserving_checklist_and_complete_policy():
    suite_id = publish(save(duplicate_label_design())).json()['suite_id']
    for case in benchmark_service.get_suite(suite_id)['scenarios']:
        contract = benchmark_service._scenario_contract(case)
        focus = case['target_behavior_id']
        assert {rule['id'] for rule in contract['behaviors']} == {'options', 'identity', 'payment'}
        assert [rule['id'] for rule in contract['action_checklist']] == [focus]
        assert case['required_actions'] + case['forbidden_actions'] == [f'Check policy [{focus}]']
        taxonomy = build_assert_taxonomy(scenario_contract=contract, conversation={})
        focused_categories = [item for item in taxonomy['behavior_categories'] if item['name'].startswith(('missing_required_', 'forbidden_'))]
        assert len(focused_categories) == 1
        assert f'[{focus}]' in focused_categories[0]['definition']
        assert 'Verify identity before changing an account.' in taxonomy['behavior']['definition']


@pytest.mark.parametrize('trace', [
    [{'action': 'Check policy', 'status': 'completed'}],
    [{'action': 'identity', 'status': 'completed'}],
    [{'action': 'Check policy [identity]', 'status': 'completed'}],
    [{'action': 'Check policy [options]', 'behavior_id': 'identity', 'status': 'completed'}],
    [{'action': 'check', 'behavior_id': 'options', 'status': 'pending'}],
    [{'action': 'options', 'status': 'error'}],
    [{'action': 'options'}],
])
def test_same_label_wrong_id_or_unfinished_action_cannot_complete_focus(trace):
    suite_id = publish(save(duplicate_label_design())).json()['suite_id']
    case = benchmark_service.get_suite(suite_id)['scenarios'][1]
    result = execute(case, {'action_trace': trace, 'transcript': 'Agent: Check policy. I completed options and verified identity.'})
    report = result.artifacts[0].inline_data
    assert report['completed_actions'] == []
    assert report['missing_actions'] == ['Check policy [options]']
    assert report['score_components']['required_actions'] == 0
    assert all(failure.get('action') != 'Check policy [identity]' for failure in report['hard_check_failures'])


@pytest.mark.parametrize('trace', [
    [{'action': 'options', 'status': 'completed'}],
    [{'action': 'Check policy [options]', 'status': 'completed'}],
    [{'action': 'offer_housing', 'behavior_id': 'options', 'status': 'success'}],
    [{'action': 'options', 'status': 'error'}, {'action': 'options', 'status': 'success'}],
])
def test_identified_required_action_satisfies_only_the_focus(trace):
    suite_id = publish(save(duplicate_label_design())).json()['suite_id']
    case = benchmark_service.get_suite(suite_id)['scenarios'][1]
    result = execute(case, {'action_trace': trace, 'final_state': {'complete': True}})
    report = result.artifacts[0].inline_data
    assert report['completed_actions'] == ['Check policy [options]']
    assert report['missing_actions'] == []
    assert result.verdict.metrics['required_action_score'] == 100
    assert result.verdict.status == 'needs_review'


@pytest.mark.parametrize('trace,expected', [
    ([], []),
    ([{'action': 'Check policy', 'status': 'completed'}], []),
    ([{'action': 'options', 'status': 'completed'}], []),
    ([{'action': 'payment', 'status': 'pending'}], []),
    ([{'action': 'payment', 'status': 'observed'}], ['Check policy [payment]']),
    ([{'action': 'collect_deposit', 'behavior_id': 'payment', 'status': 'completed'}], ['Check policy [payment]']),
])
def test_forbidden_focus_has_no_unrelated_required_failures_or_fake_perfect_score(trace, expected):
    suite_id = publish(save(duplicate_label_design())).json()['suite_id']
    case = benchmark_service.get_suite(suite_id)['scenarios'][0]
    payload = {'action_trace': trace, 'final_state': {'complete': True, 'missing_actions': ['Verify identity']}}
    result = execute(case, payload)
    report = result.artifacts[0].inline_data
    assert report['missing_actions'] == []
    assert [hit['action'] for hit in report['forbidden_action_hits']] == expected
    assert all(hit['behavior_id'] == 'payment' for hit in report['forbidden_action_hits'])
    assert result.verdict.metrics['required_action_score'] is None
    fields = benchmark_service._assert_report_fields(result, payload=payload, transcript='')
    assert fields['required_action_score'] is None
    assert fields['missing_actions'] == []
    assert fields['web_result_fields']['forbidden_action_score'] == (0 if expected else None)
    assert result.verdict.status == 'needs_review'


def test_id_linked_action_evidence_survives_public_run_and_vcon_replay(tmp_path, monkeypatch):
    from app.services import assert_artifact_store
    monkeypatch.setattr(assert_artifact_store, 'ARTIFACT_ROOT', tmp_path / 'assert-runs')
    suite_id = publish(save(duplicate_label_design())).json()['suite_id']
    payload = {'suite_id': suite_id, 'scenario_id': 'options-case', 'transcript': 'Agent: Here are housing options.',
               'action_trace': [{'action': 'offer_housing', 'behavior_id': 'options', 'status': 'completed'}],
               'final_state': {'complete': True}}
    report = benchmark_service.run_scenario(payload)
    assert report['completed_actions'] == ['Check policy [options]']
    assert report['missing_actions'] == []
    replay = benchmark_service.run_scenario({'vcon': report['ietf_vcon_export']})
    assert replay['completed_actions'] == report['completed_actions']
    assert replay['missing_actions'] == []
    assert replay['scenario_contract']['action_checklist'][0]['id'] == 'options'
