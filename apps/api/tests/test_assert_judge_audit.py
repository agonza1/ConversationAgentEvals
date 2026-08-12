from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.entities import ProductAuditEvent, ProductProject, ProductWorkspace, ProductWorkspaceMember
from app.services.product_service import record_judge_request, reset_saved_runs_for_tests


client = TestClient(app)


def setup_function():
    reset_saved_runs_for_tests()


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
    assert recorded['product_project_id'] is None
    # The absent project defaults to the actual baseline entitlement rather than
    # ASSERT's separate feature requirement (`required_plan='starter'`).
    assert recorded['plan'] == 'free'
    assert recorded['status'] == 'ready'
    assert recorded['credits'] == 10
    assert recorded['provider'] == 'assert-ai'
    assert recorded['model'] == 'openai/gpt-4.1-mini'
    assert recorded['judge_output'] == '{"judge_status":"ok"}'
    assert recorded['agrees'] is False


def test_assert_audit_uses_the_persisted_project_plan():
    from app.routes import assert_sidecar

    with SessionLocal() as db:
        project = ProductProject(
            user_id='audit-user',
            project_key='audit-project',
            name='Audit project',
            plan='business',
        )
        db.add(project)
        db.commit()

        assert assert_sidecar._product_plan(
            db,
            user_id='audit-user',
            project_id='audit-project',
            product_project_id=project.id,
        ) == 'business'


def test_assert_audit_reuses_a_project_shared_with_the_workspace_member():
    from app.routes import assert_sidecar

    with SessionLocal() as db:
        workspace = ProductWorkspace(
            owner_user_id='workspace-owner',
            workspace_key='audit-workspace',
            name='Audit workspace',
            plan='business',
        )
        db.add(workspace)
        db.flush()
        db.add_all(
            [
                ProductWorkspaceMember(workspace_id=workspace.id, user_id='workspace-owner', role='owner'),
                ProductWorkspaceMember(workspace_id=workspace.id, user_id='workspace-reviewer', role='viewer'),
            ]
        )
        project = ProductProject(
            user_id='workspace-owner',
            workspace_id=workspace.id,
            project_key='shared-audit-project',
            name='Shared audit project',
            plan='business',
        )
        personal_project = ProductProject(
            user_id='workspace-reviewer',
            project_key='shared-audit-project',
            name='Personal project with colliding key',
            plan='free',
        )
        db.add_all([project, personal_project])
        db.commit()
        project_database_id = project.id
        workspace_database_id = workspace.id

        plan = assert_sidecar._product_plan(
            db,
            user_id='workspace-reviewer',
            project_id='shared-audit-project',
            product_project_id=project_database_id,
        )
        assert plan == 'business'

        record_judge_request(
            db=db,
            user_id='workspace-reviewer',
            project_id='shared-audit-project',
            plan=plan,
            status='ready',
            credits=10,
            product_project_id=project_database_id,
        )

        matching_projects = (
            db.query(ProductProject).filter(ProductProject.project_key == 'shared-audit-project').all()
        )
        assert {row.id for row in matching_projects} == {project_database_id, personal_project.id}
        event = db.query(ProductAuditEvent).filter(ProductAuditEvent.event_type == 'judge.requested').one()
        assert event.project_id == project_database_id
        assert event.workspace_id == workspace_database_id
        assert event.actor_user_id == 'workspace-reviewer'


def test_execution_run_requires_exact_project_identity_for_a_colliding_visible_key():
    with SessionLocal() as db:
        workspace = ProductWorkspace(
            owner_user_id='execution-workspace-owner',
            workspace_key='execution-workspace',
            name='Execution workspace',
            plan='team',
        )
        db.add(workspace)
        db.flush()
        db.add_all(
            [
                ProductWorkspaceMember(
                    workspace_id=workspace.id,
                    user_id='execution-workspace-owner',
                    role='owner',
                ),
                ProductWorkspaceMember(
                    workspace_id=workspace.id,
                    user_id='execution-reviewer',
                    role='viewer',
                ),
            ]
        )
        shared_project = ProductProject(
            user_id='execution-workspace-owner',
            workspace_id=workspace.id,
            project_key='default',
            name='Shared default',
            plan='team',
        )
        personal_project = ProductProject(
            user_id='execution-reviewer',
            project_key='default',
            name='Personal default',
            plan='free',
        )
        db.add_all([shared_project, personal_project])
        db.commit()
        shared_project_id = shared_project.id

    ambiguous = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'user_id': 'execution-reviewer',
            'project_id': 'default',
        },
    )
    assert ambiguous.status_code == 400
    assert 'supply product_project_id' in ambiguous.json()['detail']

    selected = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'user_id': 'execution-reviewer',
            'project_id': 'default',
            'product_project_id': shared_project_id,
        },
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()['product_project_id'] == shared_project_id
