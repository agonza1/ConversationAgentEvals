"""Synthetic saved ASSERT status/apply evidence, including the actual native node shape."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import assert_judge
from app.services import benchmark_service, execution_run_store
from app.services.assert_review_status import saved_assert_review_freshness

client = TestClient(app)


@pytest.fixture
def saved(monkeypatch, tmp_path):
    run = json.loads((Path(__file__).parent/'fixtures/assert-review-native-run.json').read_text())
    conversation = run['conversations'][0]
    review = conversation['judge_reviews'][1]
    monkeypatch.setattr(execution_run_store, 'RUNS_DIR', tmp_path)
    monkeypatch.setattr(execution_run_store, '_RUNS', {run['execution_run_id']: run})
    monkeypatch.setattr(execution_run_store, '_persist_unlocked', lambda run: None)
    monkeypatch.setattr(assert_judge, 'run_upstream_assert_judge', lambda **kw: pytest.fail('No judging'))
    monkeypatch.setattr(assert_judge, 'record_judge_request', lambda **kw: pytest.fail('No billing/audit mutation'))
    return run, conversation, review


def status(saved, owner='demo-user', review_id=None):
    run, conv, review = saved
    return client.get(f"/api/assert/runs/{run['execution_run_id']}/conversations/{conv['conversation_id']}/reviews/{review_id or review['review_id']}/status?user_id={owner}")


def apply(saved, owner='demo-user'):
    run, conv, review = saved
    return client.post(f"/api/execution/runs/{run['execution_run_id']}/conversations/{conv['conversation_id']}/judge-reviews/{review['review_id']}/apply",json={'user_id':owner,'confirm':True})


def test_current_status_is_safe_exact_identity_and_read_only(saved):
    original=deepcopy(saved)
    response=status(saved)
    assert response.status_code==200, response.text
    assert response.json()['status']=='current'
    assert response.json()['review_id']==saved[2]['review_id']
    assert set(response.json())=={'execution_run_id','conversation_id','review_id','status','reason_code','message'}
    assert 'artifacts' not in response.text and '/Users/' not in response.text
    assert saved==original


@pytest.mark.parametrize('collection', ['conversations', 'judge_reviews'])
@pytest.mark.parametrize('bad', [None, 'invalid', 42, []])
def test_apply_rejects_malformed_sibling_entries_without_mutation(saved, collection, bad):
    run, conv, _ = saved
    parent = run if collection == 'conversations' else conv
    parent[collection].insert(0, bad)
    original = deepcopy(saved)
    response = apply(saved)
    assert response.status_code == 409
    assert 'malformed' in response.json()['detail']
    assert saved == original


@pytest.mark.parametrize('entity,field,bad', [(0, 'status', []), (1, 'status', {}),
                                            (1, 'metrics_summary', 'invalid')])
def test_malformed_snapshot_input_cannot_verify_or_apply(saved, entity, field, bad):
    saved[entity][field] = bad
    original = deepcopy(saved)
    response = status(saved)
    assert response.status_code == 200
    assert response.json()['status'] == 'cannot_verify'
    assert response.json()['reason_code'] == 'malformed_input'
    assert apply(saved).status_code == 409
    assert saved == original


@pytest.mark.parametrize('change', ['transcript','tools','final_state','target','scenario'])
def test_stale_inputs_share_export_and_direct_apply_enforcement(saved,monkeypatch,change):
    run,conv,review=saved
    if change=='transcript': conv['turns'][0]['text']='changed'
    elif change=='tools': conv['action_trace'][0]['result']={'different':True}
    elif change=='final_state': conv['final_state']={'different':True}
    elif change=='target': run['agent_name']='different target'
    else:
        monkeypatch.setattr(benchmark_service,'get_scenario_contract',lambda *a: {'goal':'different scenario'})
        monkeypatch.setattr(assert_judge,'get_scenario_contract',benchmark_service.get_scenario_contract)
    assert status(saved).json()['status']=='stale'
    assert apply(saved).status_code==409
    assert review['status']=='pending_confirmation'
    assert 'evaluation_adjudication' not in conv


@pytest.mark.parametrize('missing', ['snapshot','fingerprint','provenance','verdict'])
def test_legacy_missing_input_is_unverifiable_and_cannot_apply(saved,missing):
    run,conv,review=saved
    if missing=='snapshot': review.pop('deterministic_snapshot')
    elif missing=='fingerprint': review['judge_result']['provenance'].pop('input_fingerprint')
    elif missing=='provenance': review['judge_result'].pop('provenance')
    else:
        conv['verdict']=None;conv['metrics_summary']['verdict']=None
        review['deterministic_snapshot']=execution_run_store.deterministic_evaluation_snapshot(conv)
    assert status(saved).json()['status']=='cannot_verify'
    assert apply(saved).status_code==409


def test_wrong_owner_is_not_found_before_contract(saved,monkeypatch):
    monkeypatch.setattr(assert_judge,'get_scenario_contract',lambda *a: pytest.fail('Not before access check'))
    assert status(saved,'other-user').status_code==404
    assert apply(saved,'other-user').status_code==404


def test_inaccessible_exact_project_blocks_status_and_apply(saved,monkeypatch):
    from app.routes import execution
    run,_,_=saved
    run.update(project_id='project',product_project_id='inaccessible')
    monkeypatch.setattr(assert_judge,'execution_project_accessible',lambda **kw: False)
    monkeypatch.setattr(execution,'execution_project_accessible',lambda **kw: False)
    assert status(saved).status_code==404
    assert apply(saved).status_code==404


@pytest.mark.parametrize('case', ['unknown_review', 'malformed_history', 'non_assert_review'])
def test_revoked_binding_is_non_disclosing_before_review_selection(saved, monkeypatch, case):
    from app.routes import execution
    run, conv, review = saved
    run.update(project_id='project', product_project_id='inaccessible')
    monkeypatch.setattr(execution, 'execution_project_accessible', lambda **kw: False)
    monkeypatch.setattr(execution_run_store, 'apply_judge_review', lambda *args, **kw: pytest.fail('Before authorization'))
    if case == 'unknown_review':
        conv['judge_reviews'] = []
    elif case == 'malformed_history':
        conv['judge_reviews'] = ['invalid']
    else:
        review['provider'] = 'legacy'
        review['judge_result'].pop('provenance')
    original = deepcopy(saved)
    response = apply(saved)
    assert response.status_code == 404
    assert response.json()['detail'] == 'Execution run not found.'
    assert saved == original


def test_apply_current_assert_review_is_explicit_and_auditable(saved):
    assert status(saved).json()['status']=='current'
    response=apply(saved)
    assert response.status_code==200, response.text
    assert response.json()['conversations'][0]['evaluation_adjudication']['review_id']==saved[2]['review_id']
    assert response.json()['conversations'][0]['verdict']=='needs_review'


def test_non_assert_review_keeps_original_apply_semantics(saved):
    review=saved[2]
    review['provider']='legacy';review['judge_result'].pop('provenance')
    assert apply(saved).status_code==200


@pytest.mark.parametrize('case,expected', [('unbound', 200), ('visible', 200), ('ambiguous', 404),
                                        ('revoked', 404), ('missing_stable', 404)])
def test_real_project_resolution_matches_creation_for_export_status_and_apply(saved, tmp_path, case, expected):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base, get_db
    from app.models.entities import ProductProject, ProductWorkspace, ProductWorkspaceMember
    engine = create_engine(f'sqlite:///{tmp_path / "project-access.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    def local_db():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = local_db
    run, conv, review = saved
    run['project_id'] = 'access-case'
    run.pop('product_project_id', None)
    try:
        with factory() as db:
            if case in {'visible', 'ambiguous'}:
                personal = ProductProject(user_id='demo-user', project_key='access-case')
                db.add(personal)
                db.flush()
                if case == 'visible':
                    run['product_project_id'] = personal.id
            if case in {'ambiguous', 'revoked'}:
                workspace = ProductWorkspace(owner_user_id='team-owner', workspace_key='workspace')
                db.add(workspace)
                db.flush()
                shared = ProductProject(user_id='team-owner', workspace_id=workspace.id, project_key='access-case')
                db.add(shared)
                db.flush()
                member = ProductWorkspaceMember(workspace_id=workspace.id, user_id='demo-user', role='viewer')
                db.add(member)
                db.flush()
                if case == 'revoked':
                    run['product_project_id'] = shared.id
                    db.delete(member)
            if case == 'missing_stable':
                run['product_project_id'] = 'deleted-project'
            db.commit()
        before = deepcopy(saved)
        report_url = f"/api/assert/runs/{run['execution_run_id']}/conversations/{conv['conversation_id']}/reviews/{review['review_id']}/report.html"
        assert client.get(report_url, params={'user_id': 'demo-user'}).status_code == expected
        assert status(saved).status_code == expected
        assert saved == before  # Neither export nor freshness performs a mutation.
        assert apply(saved).status_code == expected
        assert status(saved, owner='someone-else').status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
