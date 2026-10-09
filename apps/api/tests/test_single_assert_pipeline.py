"""One semantic engine for uploads and live evidence; no paid model calls."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest
from app.db.database import SessionLocal
from app.models.entities import ProductAuditEvent
from app.services import execution_run_store, benchmark_service
from app.services.evaluation_contract import recorded_contract, freeze_contract
from app.services.upstream_assert_judge import assert_judge_input_fingerprint, _review
from app.services.assert_review_status import saved_assert_review_freshness
from app.services.design_enforcement import evaluate_design


@pytest.mark.parametrize('endpoint,extra', [
    ('/run', {'suite_id': 'unknown-suite', 'scenario_id': 'unknown', 'transcript': 'User: Test'}),
    ('/run', {'suite_id': 'call-center-voice-ai', 'scenario_id': 'unknown', 'transcript': 'User: Test'}),
    ('/simulate', {'suite_id': 'unknown-suite', 'scenario_id': 'unknown'}),
    ('/simulate', {'suite_id': 'call-center-voice-ai', 'scenario_id': 'unknown'}),
    ('/unknown-suite/run', {}),
    ('/unknown-suite/run-async', {}),
    ('/unknown-suite/simulate', {}),
    ('/unknown-suite/simulate-async', {}),
    ('/call-center-voice-ai/run', {}),
    ('/call-center-voice-ai/run-async', {}),
    ('/call-center-voice-ai/run-async', {'scenario_evidence': {'refund-policy-boundary': {}}}),
])
def test_invalid_benchmark_does_not_create_orphan_project(assert_pipeline, endpoint, extra):
    from app.models.entities import ProductProject
    p = assert_pipeline
    user, project = 'invalid-benchmark-owner', 'invalid-benchmark-project'
    with SessionLocal() as db:
        assert db.query(ProductProject).filter_by(user_id=user, project_key=project).count() == 0
    response = p.client.post('/api/benchmarks' + endpoint,
                             json={'user_id': user, 'project_id': project, **extra})
    assert response.status_code in {404, 422}, response.text
    with SessionLocal() as db:
        assert db.query(ProductProject).filter_by(user_id=user, project_key=project).count() == 0
    assert not p.calls


def test_first_benchmark_binding_is_stable_on_repeat_and_saved_judge(assert_pipeline):
    """Persist a new project identity before hashing any benchmark artifacts."""
    from app.models.entities import ProductProject
    from app.services.benchmark_run_store import get_benchmark_run

    p = assert_pipeline
    user, project = 'first-binding-owner', 'first-binding-project'
    first = p.evaluate(user_id=user, project_id=project)
    binding = first['run_metadata']['product_project_id']
    assert binding
    with SessionLocal() as db:
        records = db.query(ProductProject).filter_by(user_id=user, project_key=project).all()
        assert len(records) == 1 and records[0].id == binding
        assert get_benchmark_run(db=db, user_id=user, run_id=first['run_id'])['report']['run_metadata']['product_project_id'] == binding

    # This second payload models a reloaded UI which now knows the project UUID.
    second = p.evaluate(user_id=user, project_id=project, product_project_id=binding)
    assert second['run_id'] == first['run_id']
    assert second['logical_run_id'] == first['logical_run_id']
    assert second['run_metadata'] == first['run_metadata']
    first_judge = p.judge(first)
    assert first_judge.status_code == 200, first_judge.text
    repeat_judge = p.judge(second)
    assert repeat_judge.status_code == 200, repeat_judge.text
    assert repeat_judge.json()['review_id'] == first_judge.json()['review_id']
    assert repeat_judge.json()['reused'] is True
    assert len(p.calls) == 1 and p.spent() == 10


def test_vcon_only_target_and_whitespace_aliases_still_evaluate(assert_pipeline):
    p = assert_pipeline
    sample = p.client.get('/api/benchmarks/evidence/sample-vcon', params={
        'suite_id': 'call-center-voice-ai', 'scenario_id': 'refund-policy-boundary'}).json()
    response = p.client.post('/api/benchmarks/run', json={
        'vcon': sample, 'user_id': 'vcon-target-owner', 'project_id': 'vcon-target-project'})
    assert response.status_code == 200, response.text
    aliases = p.client.post('/api/benchmarks/run', json={
        'suiteId': ' call-center-voice-ai ', 'scenarioId': ' refund-policy-boundary ',
        'transcript': 'User: Refund? Agent: Review is pending.',
        'user_id': 'alias-target-owner', 'project_id': 'alias-target-project'})
    assert aliases.status_code == 200, aliases.text


def test_equal_project_keys_keep_distinct_bound_benchmark_identifiers(assert_pipeline):
    """Explicit personal/workspace bindings cannot collapse to one benchmark ID."""
    from app.models.entities import ProductProject, ProductWorkspace, ProductWorkspaceMember

    p = assert_pipeline
    user, project = 'namespace-reviewer', 'same-project-key'
    with SessionLocal() as db:
        workspace = ProductWorkspace(owner_user_id='namespace-workspace-owner',
                                     workspace_key='unique-namespace-workspace', name='Shared')
        db.add(workspace)
        db.flush()
        personal = ProductProject(user_id=user, project_key=project,
                                  name='Personal', plan='free')
        shared = ProductProject(user_id='namespace-workspace-owner',
                                workspace_id=workspace.id, project_key=project,
                                name='Shared', plan='team')
        db.add_all([personal, shared,
                    ProductWorkspaceMember(workspace_id=workspace.id, user_id=user, role='viewer')])
        db.commit()
        personal_id, shared_id = personal.id, shared.id

    data = {'suite_id': 'call-center-voice-ai', 'scenario_id': 'refund-policy-boundary',
            'user_id': user, 'project_id': project,
            'transcript': 'User: Refund?\\nAgent: A review is pending.'}
    ambiguous = p.client.post('/api/benchmarks/run', json=data)
    assert ambiguous.status_code == 422
    assert 'ambiguous' in ambiguous.json()['detail']

    personal_run = p.evaluate(user_id=user, project_id=project,
                              product_project_id=personal_id, transcript=data['transcript'])
    shared_run = p.evaluate(user_id=user, project_id=project,
                            product_project_id=shared_id, transcript=data['transcript'])
    assert personal_run['run_id'] != shared_run['run_id']
    assert personal_run['run_metadata']['product_project_id'] == personal_id
    assert shared_run['run_metadata']['product_project_id'] == shared_id


def test_simulation_binds_project_before_its_report_id(assert_pipeline):
    p = assert_pipeline
    body = {
        'suite_id': 'call-center-voice-ai',
        'scenario_id': 'refund-policy-boundary',
        'user_id': 'simulation-binding-owner',
        'project_id': 'simulation-binding-project',
    }
    first = p.client.post('/api/benchmarks/simulate', json=body)
    assert first.status_code == 200, first.text
    first_report = first.json()['benchmark_report']
    binding = first_report['run_metadata']['product_project_id']
    assert binding
    second = p.client.post('/api/benchmarks/simulate', json={**body, 'product_project_id': binding})
    assert second.status_code == 200, second.text
    second_report = second.json()['benchmark_report']
    assert second_report['run_id'] == first_report['run_id']
    assert second_report['logical_run_id'] == first_report['logical_run_id']


def test_legacy_execution_review_persists_binding_across_reload_and_key_collision(assert_pipeline):
    """Project created during the first judge must remain selectable forever."""
    from app.models.entities import ProductProject, ProductWorkspace, ProductWorkspaceMember
    from app.schemas.execution import ConversationRecord, ExecutionRunRecord, ExecutionRunProgress

    p = assert_pipeline
    user, project = 'late-project-owner', 'late-project-key'
    run_id = 'unbound-execution-project-158'
    conversation_id = run_id + '-conversation'
    contract = freeze_contract({'goal': 'Explain refund review without promising completion.'})
    conversation = ConversationRecord(
        conversation_id=conversation_id, execution_run_id=run_id,
        suite_id='call-center-voice-ai', scenario_id='refund-policy-boundary',
        mode='text_callable', status='needs_review',
        transcript='User: Is the refund complete?\\nAgent: The review is still pending.',
        verdict='needs_review', score=50,
        evaluation_findings={'evaluation_contract_snapshot': contract},
    )
    execution_run_store.create_execution_run(ExecutionRunRecord(
        execution_run_id=run_id, status='needs_review', mode='text_callable',
        suite_id='call-center-voice-ai', scenario_ids=['refund-policy-boundary'],
        user_id=user, project_id=project,
        conversations=[conversation],
        progress=ExecutionRunProgress(phase='completed', completed_conversations=1,
                                      total_conversations=1, percent=100),
        created_at='2026-10-08T10:00:00+00:00',
        updated_at='2026-10-08T10:00:00+00:00',
        completed_at='2026-10-08T10:00:00+00:00',
    ))
    with SessionLocal() as db:
        assert not db.query(ProductProject).filter_by(user_id=user, project_key=project).all()

    route = f'/api/assert/runs/{run_id}/conversations/{conversation_id}'
    response = p.client.post(route + '/judge', json={'user_id': user})
    assert response.status_code == 200, response.text
    review_id = response.json()['review_id']
    with SessionLocal() as db:
        saved_project = db.query(ProductProject).filter_by(
            user_id=user, project_key=project).one()
        binding = saved_project.id
    assert execution_run_store.get_execution_run(run_id)['product_project_id'] == binding

    # Simulate a process reload and the later appearance of a same-key workspace.
    execution_run_store.reset_execution_runs_for_tests()
    assert execution_run_store.get_execution_run(run_id)['product_project_id'] == binding
    with SessionLocal() as db:
        workspace = ProductWorkspace(owner_user_id='another-binding-owner',
                                     workspace_key='binding-shared-workspace',
                                     name='Shared project')
        db.add(workspace)
        db.flush()
        db.add(ProductWorkspaceMember(workspace_id=workspace.id, user_id=user, role='viewer'))
        db.add(ProductProject(user_id='another-binding-owner',
                              workspace_id=workspace.id, project_key=project,
                              name='Duplicate key', plan='team'))
        db.commit()

    assert p.client.get(route + f'/reviews/{review_id}/status',
                        params={'user_id': user}).status_code == 200
    assert p.client.get(route + f'/reviews/{review_id}/report.html',
                        params={'user_id': user}).status_code == 200
    retry = p.client.post(route + '/judge', json={'user_id': user})
    assert retry.status_code == 200, retry.text
    assert retry.json()['review_id'] == review_id
    assert retry.json()['reused'] is True
    applied = p.client.post(
        f'/api/execution/runs/{run_id}/conversations/{conversation_id}/judge-reviews/{review_id}/apply',
        json={'user_id': user, 'confirm': True})
    assert applied.status_code == 200, applied.text
    assert len(p.calls) == 1 and p.spent() == 10
    with pytest.raises(ValueError, match='already bound'):
        execution_run_store.bind_execution_run_product_project(
            run_id, user_id=user, project_id=project, product_project_id='different-project-id')


def test_uploaded_transcript_and_live_review_share_assert_and_dedupe(assert_pipeline):
    p = assert_pipeline
    report = p.evaluate(transcript='User: Refund?\nAgent: This request needs review. '+('long evidence ' * 120))
    assert report['verdict'] == 'needs_review'
    assert report['semantic_review_required'] is True
    result = p.judge(report)
    assert result.status_code == 200, result.text
    first = result.json()
    assert first['provider'] == 'assert-ai' and first['review_id']
    assert p.spent() == 10 and len(p.calls) == 1
    run_id, conv_id = first['execution_run_id'], first['conversation_id']
    conv = execution_run_store.get_conversation(run_id, conv_id)
    assert len(conv['transcript']) > 700  # No preview truncation.
    assert conv['action_trace'] == [] and conv['final_state'] == {}
    assert execution_run_store.get_execution_run(run_id)['executor_id'] == 'evidence_replay'
    second = p.client.post(f'/api/assert/runs/{run_id}/conversations/{conv_id}/judge', json={'user_id': 'owner'})
    assert second.status_code == 200, second.text
    assert second.json()['review_id'] == first['review_id']
    assert second.json()['reused'] is True
    assert len(p.calls) == 1 and p.spent() == 10
    with SessionLocal() as db:
        assert db.query(ProductAuditEvent).filter_by(event_type='judge.requested').count() == 1
    assert len(execution_run_store.get_conversation(run_id, conv_id)['judge_reviews']) == 1


def test_uploaded_vcon_preserves_structured_evidence_and_uses_assert(assert_pipeline):
    p = assert_pipeline
    sample = p.client.get('/api/benchmarks/evidence/sample-vcon', params={
        'suite_id': 'call-center-voice-ai', 'scenario_id': 'refund-policy-boundary'})
    assert sample.status_code == 200
    report = p.evaluate(vcon=sample.json())
    result = p.judge(report)
    assert result.status_code == 200, result.text
    saved = result.json()
    conv = execution_run_store.get_conversation(saved['execution_run_id'], saved['conversation_id'])
    assert conv['transcript'] == report['transcript']
    assert conv['action_trace'] == report['action_trace']
    assert conv['final_state'] == report['final_state']
    assert len(p.calls) == 1 and 'assert_ai.cli' in p.calls[0]


@pytest.mark.parametrize('final_state', [
    'Refund review is pending approval.',
    [{'phase': 'final', 'description': 'Pending refund review'}, 'not an execution receipt'],
])
def test_unstructured_final_state_is_retained_without_inventing_a_snapshot(assert_pipeline, final_state):
    """Every accepted benchmark shape can reach ASSERT without becoming verified state."""
    from pathlib import Path
    import yaml

    pipeline = assert_pipeline
    report = pipeline.evaluate(final_state=final_state)
    assert report['final_state'] == final_state
    response = pipeline.judge(report)
    assert response.status_code == 200, response.text
    result = response.json()
    conversation = execution_run_store.get_conversation(
        result['execution_run_id'], result['conversation_id'])
    assert conversation['final_state'] == {}
    assert conversation['unstructured_final_state_evidence'] == final_state
    assert len(pipeline.calls) == 1

    command = pipeline.calls[0]
    config_path = Path(command[command.index('--config') + 1])
    config = yaml.safe_load(config_path.read_text())['pipeline']['judge']
    inference = json.loads(Path(config['inference_set_path']).read_text())
    events_json = json.dumps(inference, sort_keys=True)
    assert 'cae_unverified_final_state_evidence' in events_json
    assert 'cae_final_state_snapshot' not in events_json
    assert '"execution_verified": false' in events_json
    assert inference['dimensions']['evidence_level'] == 'black_box'


def test_structured_final_state_is_still_retained_as_observed_state(assert_pipeline):
    from pathlib import Path
    import yaml

    pipeline = assert_pipeline
    final_state = {'complete': False, 'outcome': 'refund_pending', 'case_id': 'case-123'}
    report = pipeline.evaluate(final_state=final_state)
    response = pipeline.judge(report)
    assert response.status_code == 200, response.text
    ids = response.json()
    conversation = execution_run_store.get_conversation(
        ids['execution_run_id'], ids['conversation_id'])
    assert conversation['final_state'] == final_state
    assert 'unstructured_final_state_evidence' not in conversation
    command = pipeline.calls[0]
    config = yaml.safe_load(Path(command[command.index('--config') + 1]).read_text())['pipeline']['judge']
    inference = json.loads(Path(config['inference_set_path']).read_text())
    events_json = json.dumps(inference, sort_keys=True)
    assert 'cae_final_state_snapshot' in events_json
    assert 'cae_unverified_final_state_evidence' not in events_json


def test_judge_rejects_client_supplied_report_and_other_owner_before_spending(assert_pipeline):
    p = assert_pipeline
    report = p.evaluate()
    assert p.judge(report, report={'verdict': 'pass'}).status_code == 422
    assert p.judge(report, user_id='intruder').status_code == 404
    assert len(p.calls) == 0 and p.spent() == 0
    assert p.client.post('/api/product/judge', json={'report': report}).status_code == 404


def test_readiness_and_judge_use_credentials_not_oauth(assert_pipeline, monkeypatch):
    p = assert_pipeline
    from app.services import product_service
    monkeypatch.setattr(product_service, '_openai_provider_status', lambda: {'status': 'connected'})
    report = p.evaluate()
    monkeypatch.delenv('LLM_JUDGE_API_KEY')
    readiness = p.client.get('/api/assert/readiness').json()
    assert readiness['ready'] is False and readiness['credentials_ready'] is False
    assert p.client.get('/api/product/config').json()['llm_judge_status'] == 'gated'
    assert p.judge(report).status_code == 503
    assert p.spent() == 0 and not p.calls
    monkeypatch.setenv('LLM_JUDGE_API_KEY', 'synthetic-key')
    assert p.client.get('/api/assert/readiness').json()['ready'] is True
    assert p.judge(report).status_code == 200  # Failed admission is retryable, not cached forever.


def test_budget_zero_is_not_replaced_by_default(assert_pipeline, monkeypatch):
    p = assert_pipeline
    report = p.evaluate()
    monkeypatch.setenv('LLM_JUDGE_DAILY_CREDIT_LIMIT', '0')
    assert p.judge(report).status_code == 429
    assert not p.calls and p.spent() == 0


def test_retry_failure_and_explicit_new_sample(assert_pipeline):
    p = assert_pipeline
    report = p.evaluate()
    p.raise_error = RuntimeError('synthetic provider failure')
    assert p.judge(report, request_id='sample-0001').status_code == 502
    assert p.spent() == 0
    assert report['verdict'] == 'needs_review'  # Evaluator failure does not become agent failure.
    p.raise_error = None
    first = p.judge(report, request_id='sample-0001')
    assert first.status_code == 200, first.text
    assert p.judge(report, request_id='sample-0001').json()['review_id'] == first.json()['review_id']
    other = p.judge(report, request_id='sample-0002')
    assert other.status_code == 200 and other.json()['review_id'] != first.json()['review_id']
    assert p.spent() == 20 and len(p.calls) == 3  # One failure + two paid successful samples.


def test_concurrent_duplicate_starts_one_provider_call(assert_pipeline):
    p = assert_pipeline
    report = p.evaluate()
    started, release = Event(), Event()
    def pause():
        started.set()
        assert release.wait(10)
    p.pause = pause
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(p.judge, report)
        try:
            assert started.wait(10)
            second = pool.submit(p.judge, report).result(timeout=10)
            assert second.status_code == 409, second.text
        finally:
            release.set()
        original = first.result(timeout=10)
    assert original.status_code == 200, original.text
    assert p.judge(report).json()['review_id'] == original.json()['review_id']
    assert len(p.calls) == 1 and p.spent() == 10


def test_catalog_changes_do_not_change_saved_judge_contract(assert_pipeline, monkeypatch):
    p = assert_pipeline
    report = p.evaluate()
    # The judge and freshness/export/apply must never read the current catalog.
    monkeypatch.setattr(benchmark_service, 'get_scenario_contract', lambda *a: pytest.fail('Mutable catalog read'))
    response = p.judge(report)
    assert response.status_code == 200, response.text
    result = response.json()
    run = execution_run_store.get_execution_run(result['execution_run_id'])
    conv = run['conversations'][0]
    review = conv['judge_reviews'][0]
    assert recorded_contract(conv)['_snapshot_sha256'] == report['evaluation_contract_snapshot']['sha256']
    assert saved_assert_review_freshness(run, conv, review)['status'] == 'current'
    export = p.client.get(f"/api/assert/runs/{run['execution_run_id']}/conversations/{conv['conversation_id']}/reviews/{review['review_id']}/report.html?user_id=owner")
    assert export.status_code == 200, export.text
    original = deepcopy(conv)
    apply = p.client.post(f"/api/execution/runs/{run['execution_run_id']}/conversations/{conv['conversation_id']}/judge-reviews/{review['review_id']}/apply", json={'user_id': 'owner', 'confirm': True})
    assert apply.status_code == 200, apply.text
    assert apply.json()['conversations'][0]['evaluation_findings'] == original['evaluation_findings']


def test_complete_fingerprint_changes_with_grader_not_only_evidence(assert_pipeline, monkeypatch):
    p = assert_pipeline
    result = p.judge(p.evaluate()).json()
    run = execution_run_store.get_execution_run(result['execution_run_id']); conv = run['conversations'][0]
    review = conv['judge_reviews'][0]
    monkeypatch.setenv('ASSERT_JUDGE_MAX_TOKENS', '4200')
    assert saved_assert_review_freshness(run, conv, review)['reason_code'] == 'grader_changed'
    assert p.client.post(f"/api/execution/runs/{run['execution_run_id']}/conversations/{conv['conversation_id']}/judge-reviews/{review['review_id']}/apply", json={'user_id': 'owner', 'confirm': True}).status_code == 409


def test_missing_evidence_is_not_failure_and_inapplicable_is_distinct():
    scenario = {'deterministic_checks': [
        {'id': 'tool', 'label': 'tool_succeeded:refund'},
        {'id': 'optional', 'label': 'final_state_complete', 'applicable': False}], 'behaviors': []}
    result = evaluate_design(scenario, {}, 'Agent: I refunded it.')
    checks = result['programmatic_check_results']
    assert checks[0]['outcome'] == 'not_observable'
    assert checks[1]['outcome'] == 'not_applicable'
    assert result['design_enforcement']['failed'] is False
    failed = evaluate_design(scenario, {'action_trace': [{'tool_name': 'refund', 'status': 'failed'}]}, 'Agent: Done.')
    assert failed['programmatic_check_results'][0]['outcome'] == 'fail'
    assert failed['design_enforcement']['failed'] is True


def test_unverified_outcome_is_review_not_a_false_claim_or_semantic_pass():
    score = {'verdict': {'dimensions': {'unverified_operational_outcome': True, 'unsupported_operational_claim': False},
                        'dimension_justifications': {'unverified_operational_outcome': 'No telemetry', 'unsupported_operational_claim': 'No contradiction'},
                        'node_judgments': [{'node_name': 'test', 'violated': False}], 'narrative': 'Unverified'}}
    result = _review(score, 'needs_review', allow_semantic_pass=True)
    assert result['proposed_evaluation']['verdict'] == 'needs_review'
    assert result['check_results'][0]['outcome'] == 'not_observable'
    assert result['check_results'][1]['outcome'] == 'pass'  # No proven contradiction, not verified execution.
    assert _review(score, 'fail', allow_semantic_pass=True)['proposed_evaluation']['verdict'] == 'fail'


def test_changed_contract_creates_new_evaluation_not_mutated_old_review(assert_pipeline, monkeypatch):
    p = assert_pipeline
    original = p.evaluate()
    first = p.judge(original).json()
    scenario = benchmark_service._SCENARIOS_BY_ID[('call-center-voice-ai', 'refund-policy-boundary')]
    monkeypatch.setitem(scenario, 'required_actions', [*scenario['required_actions'], 'explain new policy'])
    newer = p.evaluate()
    assert newer['run_id'] != original['run_id']
    assert newer['evaluation_contract_snapshot']['sha256'] != original['evaluation_contract_snapshot']['sha256']
    retry = p.judge(original)
    assert retry.status_code == 200 and retry.json()['review_id'] == first['review_id']
    assert len(p.calls) == 1


@pytest.mark.parametrize('field', ['dimensions', 'adapter_version', 'assert_version', 'model_settings'])
def test_full_fingerprint_covers_grader_configuration(assert_pipeline, monkeypatch, field):
    from app.services import upstream_assert_judge as judge
    p = assert_pipeline
    first = p.judge(p.evaluate()).json()
    run = execution_run_store.get_execution_run(first['execution_run_id']); conv = run['conversations'][0]
    original = judge.judge_configuration
    def modified(model, n):
        value = original(model, n)
        value[field] = {'changed': True}
        return value
    monkeypatch.setattr(judge, 'judge_configuration', modified)
    assert saved_assert_review_freshness(run, conv, conv['judge_reviews'][0])['status'] == 'stale'


def test_retry_after_audit_failure_reuses_retained_provider_result(assert_pipeline, monkeypatch):
    from app.routes import assert_judge
    p = assert_pipeline
    original = assert_judge.record_judge_request
    def unavailable(**kwargs):
        raise RuntimeError('Synthetic audit storage outage')
    report = p.evaluate()
    monkeypatch.setattr(assert_judge, 'record_judge_request', unavailable)
    with pytest.raises(RuntimeError, match='Synthetic audit'):
        p.judge(report)
    assert len(p.calls) == 1 and p.spent() == 10
    monkeypatch.setattr(assert_judge, 'record_judge_request', original)
    result = p.judge(report)
    assert result.status_code == 200 and result.json()['reused'] is True
    assert len(p.calls) == 1 and p.spent() == 10
    run = execution_run_store.get_execution_run(result.json()['execution_run_id'])
    assert len(run['conversations'][0]['judge_reviews']) == 1


def test_revoked_exact_project_blocks_cached_upload_review(assert_pipeline):
    from app.models.entities import ProductProject
    p = assert_pipeline
    report = p.evaluate()
    assert p.judge(report).status_code == 200
    with SessionLocal() as db:
        project = db.query(ProductProject).filter_by(user_id='owner', project_key='project').one()
        project.user_id = 'another-owner'
        db.commit()
    assert p.judge(report).status_code in {404, 409}
    assert len(p.calls) == 1 and p.spent() == 10


def test_non_openai_readiness_uses_pinned_provider_adapter_without_a_model_probe(assert_pipeline, monkeypatch):
    from app.services import upstream_assert_judge as judge
    import litellm
    validated = []
    monkeypatch.setenv('ASSERT_JUDGE_MODEL', 'ollama/example-local-model')
    monkeypatch.setenv('OLLAMA_API_BASE', 'http://127.0.0.1:11434')
    monkeypatch.setattr(litellm, 'validate_environment', lambda **kw: validated.append(kw) or {'keys_in_environment': True, 'missing_keys': []})
    assert judge.assert_judge_readiness()['ready'] is True
    assert validated == [{'model': 'ollama/example-local-model'}]
    assert not assert_pipeline.calls


def test_provider_urls_are_not_persisted_in_assert_provenance(assert_pipeline, monkeypatch):
    """The same routing identity must invalidate stale reviews without leaking gateways or credentials."""
    from app.services import upstream_assert_judge as judge
    import re

    pipeline = assert_pipeline
    secret = 'do-not-store-provider-credential-321'
    hostname = 'private-provider-gateway.internal'
    url = f'https://service-user:{secret}@{hostname}/v1?token={secret}'
    monkeypatch.setenv('OPENAI_BASE_URL', url)
    monkeypatch.setenv('VERTEXAI_PROJECT', 'private-cloud-project-123')

    configuration = judge.judge_configuration('openai/gpt-4.1-mini', 1)
    identity = configuration['provider_endpoint_identity_sha256']
    assert re.fullmatch(r'[0-9a-f]{64}', identity)
    assert configuration == judge.judge_configuration('openai/gpt-4.1-mini', 1)
    assert 'provider_endpoints' not in configuration
    for private_value in (secret, hostname, 'service-user', 'private-cloud-project-123'):
        assert private_value not in json.dumps(configuration)

    judged = pipeline.judge(pipeline.evaluate())
    assert judged.status_code == 200, judged.text
    response = judged.json()
    run = execution_run_store.get_execution_run(response['execution_run_id'])
    conversation = run['conversations'][0]
    review = conversation['judge_reviews'][0]
    assert review['judge_result']['provenance']['configuration'] == configuration
    for private_value in (secret, hostname, 'service-user', 'private-cloud-project-123'):
        assert private_value not in json.dumps(response)
        assert private_value not in json.dumps(review)

    monkeypatch.setenv('OPENAI_BASE_URL', 'https://a-different-provider.internal/v1')
    assert judge.judge_configuration('openai/gpt-4.1-mini', 1)['provider_endpoint_identity_sha256'] != identity
    assert saved_assert_review_freshness(run, conversation, review)['reason_code'] == 'grader_changed'
