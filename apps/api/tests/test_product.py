import json

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.db.database import SessionLocal
from app.main import app
from app.models.entities import ProductProject, ProductSavedRun
from app.services.product_service import reset_saved_runs_for_tests

client = TestClient(app)


def setup_function():
    reset_saved_runs_for_tests()


def test_product_config_exposes_pricing_auth_and_usage_gates():
    response = client.get('/api/product/config')

    assert response.status_code == 200
    payload = response.json()
    plans = {plan['id']: plan for plan in payload['pricing']}
    assert plans['starter']['price_label'] == '$19/month'
    assert plans['team']['price_label'] == '$99/month'
    assert plans['starter']['seats'] == 'Unlimited seats'
    assert plans['business']['price_label'] == 'Contact Us'
    assert payload['auth']['enabled'] is True
    assert payload['auth']['providers'] == ['email_link', 'google']
    assert {rule['id'] for rule in payload['usage_rules']} >= {'deterministic_eval', 'llm_judge', 'voice_webrtc_minute'}


def test_product_config_exposes_configured_stripe_price_ids(monkeypatch):
    monkeypatch.setenv('STRIPE_STARTER_PRICE_ID', 'price_starter_123')
    monkeypatch.setenv('STRIPE_TEAM_PRICE_ID', 'price_team_456')

    response = client.get('/api/product/config')

    assert response.status_code == 200
    plans = {plan['id']: plan for plan in response.json()['pricing']}
    assert plans['starter']['stripe_price_id'] == 'price_starter_123'
    assert plans['team']['stripe_price_id'] == 'price_team_456'
    assert plans['free']['stripe_price_id'] is None


def test_checkout_gate_blocks_until_plan_price_is_configured(monkeypatch):
    monkeypatch.delenv('STRIPE_STARTER_PRICE_ID', raising=False)

    response = client.post(
        '/api/product/checkout',
        json={'plan': 'starter', 'user_id': 'demo-user', 'project_id': 'call-center'},
    )

    assert response.status_code == 200
    assert response.json() == {
        'status': 'blocked',
        'plan': 'starter',
        'stripe_price_id': None,
        'checkout_url': None,
        'mode': 'subscription',
        'message': 'Stripe checkout is not configured for this plan yet.',
        'metadata': {'user_id': 'demo-user', 'project_id': 'call-center', 'plan': 'starter'},
    }


def test_checkout_gate_reports_ready_when_plan_price_is_configured(monkeypatch):
    monkeypatch.setenv('STRIPE_TEAM_PRICE_ID', 'price_team_456')
    monkeypatch.setenv('STRIPE_CHECKOUT_BASE_URL', 'https://billing.example.com/checkout')

    response = client.post(
        '/api/product/checkout',
        json={
            'plan': 'team',
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'success_url': 'https://app.example.com/success',
            'cancel_url': 'https://app.example.com/cancel',
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        'status': 'ready',
        'plan': 'team',
        'stripe_price_id': 'price_team_456',
        'checkout_url': 'https://billing.example.com/checkout?price_id=price_team_456&client_reference_id=demo-user%3Acall-center&success_url=https%3A%2F%2Fapp.example.com%2Fsuccess&cancel_url=https%3A%2F%2Fapp.example.com%2Fcancel',
        'mode': 'subscription',
        'message': 'Stripe price is configured and ready for checkout session creation.',
        'metadata': {
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'team',
            'success_url': 'https://app.example.com/success',
            'cancel_url': 'https://app.example.com/cancel',
        },
    }


def test_workspaces_create_owner_membership_defaults_and_list_by_member():
    response = client.post(
        '/api/product/workspaces',
        json={
            'owner_user_id': 'owner-user',
            'workspace_id': 'acme-support',
            'name': 'Acme Support QA',
            'plan': 'team',
            'settings': {'retention_days': 180},
            'onboarding': {'next_step': 'invite_teammate'},
        },
    )

    assert response.status_code == 200
    workspace = response.json()
    assert workspace['workspace_id'] == 'acme-support'
    assert workspace['settings']['default_benchmark_suite'] == 'call-center-voice-ai'
    assert workspace['settings']['retention_days'] == 180
    assert workspace['onboarding']['next_step'] == 'invite_teammate'
    assert workspace['members'][0]['user_id'] == 'owner-user'
    assert workspace['members'][0]['role'] == 'owner'

    list_response = client.get('/api/product/workspaces', params={'user_id': 'owner-user'})

    assert list_response.status_code == 200
    assert [item['id'] for item in list_response.json()] == [workspace['id']]


def test_workspace_admins_can_add_members_and_invite_users():
    workspace = client.post(
        '/api/product/workspaces',
        json={
            'owner_user_id': 'owner-user',
            'workspace_id': 'acme-support',
            'name': 'Acme Support QA',
            'plan': 'team',
        },
    ).json()

    member_response = client.post(
        f"/api/product/workspaces/{workspace['id']}/members",
        json={'requester_user_id': 'owner-user', 'user_id': 'qa-lead', 'role': 'admin'},
    )

    assert member_response.status_code == 200
    members = {member['user_id']: member['role'] for member in member_response.json()['members']}
    assert members == {'owner-user': 'owner', 'qa-lead': 'admin'}

    invite_response = client.post(
        f"/api/product/workspaces/{workspace['id']}/invitations",
        json={'requester_user_id': 'qa-lead', 'email': 'Reviewer@Example.COM', 'role': 'viewer'},
    )

    assert invite_response.status_code == 200
    invitation = invite_response.json()
    assert invitation['email'] == 'reviewer@example.com'
    assert invitation['role'] == 'viewer'
    assert invitation['status'] == 'pending'
    assert invitation['invited_by_user_id'] == 'qa-lead'

    listed = client.get('/api/product/workspaces', params={'user_id': 'qa-lead'}).json()
    assert listed[0]['invitations'][0]['email'] == 'reviewer@example.com'


def test_invited_users_can_accept_workspace_invitation_once():
    workspace = client.post(
        '/api/product/workspaces',
        json={
            'owner_user_id': 'owner-user',
            'workspace_id': 'acme-support',
            'name': 'Acme Support QA',
            'plan': 'team',
        },
    ).json()
    invitation = client.post(
        f"/api/product/workspaces/{workspace['id']}/invitations",
        json={'requester_user_id': 'owner-user', 'email': 'Reviewer@Example.COM', 'role': 'editor'},
    ).json()

    accepted = client.post(
        f"/api/product/workspaces/{workspace['id']}/invitations/{invitation['id']}/accept",
        json={'user_id': 'reviewer-user', 'email': 'reviewer@example.com'},
    )

    assert accepted.status_code == 200
    payload = accepted.json()
    members = {member['user_id']: member['role'] for member in payload['members']}
    assert members == {'owner-user': 'owner', 'reviewer-user': 'editor'}
    assert payload['invitations'][0]['status'] == 'accepted'

    listed = client.get('/api/product/workspaces', params={'user_id': 'reviewer-user'})
    assert listed.status_code == 200
    assert listed.json()[0]['id'] == workspace['id']

    repeated = client.post(
        f"/api/product/workspaces/{workspace['id']}/invitations/{invitation['id']}/accept",
        json={'user_id': 'another-user', 'email': 'reviewer@example.com'},
    )
    wrong_email = client.post(
        f"/api/product/workspaces/{workspace['id']}/invitations/{invitation['id']}/accept",
        json={'user_id': 'another-user', 'email': 'other@example.com'},
    )

    assert repeated.status_code == 404
    assert wrong_email.status_code == 404


def test_workspace_viewers_cannot_manage_members_or_invitations():
    workspace = client.post(
        '/api/product/workspaces',
        json={
            'owner_user_id': 'owner-user',
            'workspace_id': 'acme-support',
            'name': 'Acme Support QA',
            'plan': 'team',
        },
    ).json()
    add_viewer = client.post(
        f"/api/product/workspaces/{workspace['id']}/members",
        json={'requester_user_id': 'owner-user', 'user_id': 'viewer-user', 'role': 'viewer'},
    )
    assert add_viewer.status_code == 200

    blocked_member = client.post(
        f"/api/product/workspaces/{workspace['id']}/members",
        json={'requester_user_id': 'viewer-user', 'user_id': 'other-user', 'role': 'editor'},
    )
    blocked_invite = client.post(
        f"/api/product/workspaces/{workspace['id']}/invitations",
        json={'requester_user_id': 'viewer-user', 'email': 'other@example.com', 'role': 'viewer'},
    )

    assert blocked_member.status_code == 404
    assert blocked_invite.status_code == 404


def test_projects_store_workspace_and_onboarding_settings():
    workspace = client.post(
        '/api/product/workspaces',
        json={
            'owner_user_id': 'demo-user',
            'workspace_id': 'acme-support',
            'name': 'Acme Support QA',
            'plan': 'starter',
        },
    ).json()
    project_response = client.post(
        '/api/product/projects',
        json={
            'user_id': 'demo-user',
            'workspace_id': workspace['id'],
            'project_id': 'call-center',
            'name': 'Call Center QA',
            'plan': 'starter',
            'settings': {'report_visibility': 'private'},
            'onboarding': {'next_step': 'run_benchmark'},
        },
    )

    assert project_response.status_code == 200
    project = project_response.json()
    assert project['workspace_id'] == workspace['id']
    assert project['settings']['report_visibility'] == 'private'
    assert project['settings']['retention_days'] == 90
    assert project['onboarding']['sample_project_created'] is True

    patch_response = client.patch(
        '/api/product/projects/call-center/settings',
        json={
            'user_id': 'demo-user',
            'settings': {'retention_days': 365},
            'onboarding': {'next_step': 'export_report'},
        },
    )

    assert patch_response.status_code == 200
    patched = patch_response.json()
    assert patched['settings']['retention_days'] == 365
    assert patched['settings']['default_benchmark_suite'] == 'call-center-voice-ai'
    assert patched['onboarding']['next_step'] == 'export_report'

    missing = client.patch(
        '/api/product/projects/call-center/settings',
        json={'user_id': 'other-user', 'settings': {}, 'onboarding': {}},
    )
    assert missing.status_code == 404


def test_project_export_recommends_first_suite_scenario_when_history_is_empty():
    project_response = client.post(
        '/api/product/projects',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'name': 'Call Center QA',
            'plan': 'free',
        },
    )
    assert project_response.status_code == 200

    export_response = client.get(
        '/api/product/projects/call-center/export',
        params={'user_id': 'demo-user', 'suite_id': 'call-center-voice-ai'},
    )

    assert export_response.status_code == 200
    coverage = export_response.json()['scenario_coverage_summary']
    assert coverage['covered_scenario_count'] == 0
    assert coverage['coverage_percent'] == 0.0
    assert coverage['coverage_status'] == 'empty'
    assert coverage['recommended_next_scenario'] == {
        'id': 'billing-address-change',
        'title': 'Billing Address Change',
    }


def test_project_export_marks_suite_coverage_complete_when_all_scenarios_are_saved():
    for index, scenario_id in enumerate([
        'billing-address-change',
        'angry-outage-escalation',
        'interruption-correction-handling',
        'refund-policy-boundary',
    ]):
        response = client.post(
            '/api/product/runs',
            json={
                'user_id': 'demo-user',
                'project_id': 'call-center',
                'plan': 'starter',
                'report': {
                    'run_id': f'coverage-run-{index}',
                    'overall_score': 90,
                    'suite_id': 'call-center-voice-ai',
                    'scenario_id': scenario_id,
                },
            },
        )
        assert response.status_code == 200

    export_response = client.get(
        '/api/product/projects/call-center/export',
        params={'user_id': 'demo-user', 'suite_id': 'call-center-voice-ai'},
    )

    assert export_response.status_code == 200
    coverage = export_response.json()['scenario_coverage_summary']
    assert coverage['covered_scenario_count'] == 4
    assert coverage['coverage_percent'] == 100.0
    assert coverage['coverage_status'] == 'complete'
    assert coverage['missing_scenario_ids'] == []
    assert coverage['recommended_next_scenario'] is None


def test_workspace_editors_can_update_shared_project_settings_but_viewers_cannot():
    workspace = client.post(
        '/api/product/workspaces',
        json={
            'owner_user_id': 'owner-user',
            'workspace_id': 'acme-support',
            'name': 'Acme Support QA',
            'plan': 'team',
        },
    ).json()
    project = client.post(
        '/api/product/projects',
        json={
            'user_id': 'owner-user',
            'workspace_id': workspace['id'],
            'project_id': 'call-center',
            'name': 'Call Center QA',
            'plan': 'team',
        },
    ).json()
    assert project['settings']['retention_days'] == 90

    editor = client.post(
        f"/api/product/workspaces/{workspace['id']}/members",
        json={'requester_user_id': 'owner-user', 'user_id': 'editor-user', 'role': 'editor'},
    )
    viewer = client.post(
        f"/api/product/workspaces/{workspace['id']}/members",
        json={'requester_user_id': 'owner-user', 'user_id': 'viewer-user', 'role': 'viewer'},
    )
    assert editor.status_code == 200
    assert viewer.status_code == 200

    updated = client.patch(
        '/api/product/projects/call-center/settings',
        json={
            'user_id': 'editor-user',
            'settings': {'retention_days': 30, 'report_visibility': 'workspace'},
            'onboarding': {'next_step': 'export_report'},
        },
    )
    assert updated.status_code == 200
    assert updated.json()['settings']['retention_days'] == 30
    assert updated.json()['onboarding']['next_step'] == 'export_report'

    blocked = client.patch(
        '/api/product/projects/call-center/settings',
        json={'user_id': 'viewer-user', 'settings': {'retention_days': 365}, 'onboarding': {}},
    )
    assert blocked.status_code == 404


def test_project_workspace_assignment_requires_membership():
    workspace = client.post(
        '/api/product/workspaces',
        json={
            'owner_user_id': 'owner-user',
            'workspace_id': 'acme-support',
            'name': 'Acme Support QA',
            'plan': 'team',
        },
    ).json()

    blocked = client.post(
        '/api/product/projects',
        json={
            'user_id': 'outsider-user',
            'workspace_id': workspace['id'],
            'project_id': 'call-center',
            'name': 'Call Center QA',
            'plan': 'starter',
        },
    )
    assert blocked.status_code == 404

    add_member = client.post(
        f"/api/product/workspaces/{workspace['id']}/members",
        json={'requester_user_id': 'owner-user', 'user_id': 'member-user', 'role': 'editor'},
    )
    assert add_member.status_code == 200

    allowed = client.post(
        '/api/product/projects',
        json={
            'user_id': 'member-user',
            'workspace_id': workspace['id'],
            'project_id': 'call-center',
            'name': 'Call Center QA',
            'plan': 'starter',
        },
    )
    assert allowed.status_code == 200


def test_saved_runs_are_project_scoped_and_require_user_id():
    project_response = client.post(
        '/api/product/projects',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'name': 'Call Center QA',
            'plan': 'starter',
        },
    )
    assert project_response.status_code == 200
    assert project_response.json()['project_id'] == 'call-center'

    response = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {'run_id': 'abc', 'overall_score': 92},
            'transcript': 'Agent: verified and completed the update.',
        },
    )

    assert response.status_code == 200
    saved = response.json()
    assert saved['id']
    assert saved['project_id'] == 'call-center'
    assert saved['project_name'] == 'Call Center QA'
    assert saved['firestore_path'] == f"users/demo-user/projects/call-center/runs/{saved['id']}"
    assert saved['artifacts']['overall_score'] == 92

    list_response = client.get('/api/product/runs', params={'user_id': 'demo-user', 'project_id': 'call-center'})
    assert list_response.status_code == 200
    assert [run['id'] for run in list_response.json()] == [saved['id']]

    detail_response = client.get(f"/api/product/runs/{saved['id']}", params={'user_id': 'demo-user'})
    assert detail_response.status_code == 200
    assert detail_response.json() == saved

    forbidden_detail = client.get(f"/api/product/runs/{saved['id']}", params={'user_id': 'other-user'})
    assert forbidden_detail.status_code == 404

    projects_response = client.get('/api/product/projects', params={'user_id': 'demo-user'})
    assert projects_response.status_code == 200
    assert projects_response.json()[0]['run_count'] == 1
    assert projects_response.json()[0]['last_run_at']

    missing_user = client.get('/api/product/runs')
    assert missing_user.status_code == 422


def test_workspace_members_can_read_shared_project_history_and_exports():
    workspace = client.post(
        '/api/product/workspaces',
        json={
            'owner_user_id': 'owner-user',
            'workspace_id': 'acme-support',
            'name': 'Acme Support QA',
            'plan': 'team',
        },
    ).json()
    add_member = client.post(
        f"/api/product/workspaces/{workspace['id']}/members",
        json={'requester_user_id': 'owner-user', 'user_id': 'reviewer-user', 'role': 'viewer'},
    )
    assert add_member.status_code == 200

    project = client.post(
        '/api/product/projects',
        json={
            'user_id': 'owner-user',
            'workspace_id': workspace['id'],
            'project_id': 'call-center',
            'name': 'Call Center QA',
            'plan': 'team',
        },
    ).json()
    saved = client.post(
        '/api/product/runs',
        json={
            'user_id': 'owner-user',
            'project_id': 'call-center',
            'plan': 'team',
            'report': {
                'run_id': 'abc',
                'suite_id': 'call-center-voice-ai',
                'scenario_id': 'billing-address-change',
                'overall_score': 88,
                'verdict': 'pass',
            },
            'transcript': 'Agent: verified the caller and confirmed the billing address update.',
        },
    ).json()

    projects = client.get('/api/product/projects', params={'user_id': 'reviewer-user'}).json()
    assert [(item['project_id'], item['run_count']) for item in projects] == [('call-center', 1)]
    assert projects[0]['id'] == project['id']

    runs_response = client.get('/api/product/runs', params={'user_id': 'reviewer-user', 'project_id': 'call-center'})
    assert runs_response.status_code == 200
    assert [run['id'] for run in runs_response.json()] == [saved['id']]

    detail_response = client.get(f"/api/product/runs/{saved['id']}", params={'user_id': 'reviewer-user'})
    assert detail_response.status_code == 200
    assert detail_response.json()['firestore_path'] == f"users/owner-user/projects/call-center/runs/{saved['id']}"

    summary_response = client.get(
        '/api/product/projects/call-center/regression-summary',
        params={'user_id': 'reviewer-user', 'suite_id': 'call-center-voice-ai'},
    )
    assert summary_response.status_code == 200
    assert summary_response.json()['run_count'] == 1
    assert summary_response.json()['latest_score'] == 88

    export_response = client.get('/api/product/projects/call-center/export', params={'user_id': 'reviewer-user'})
    assert export_response.status_code == 200
    assert export_response.json()['run_count'] == 1
    assert export_response.json()['runs'][0]['id'] == saved['id']

    outsider_response = client.get('/api/product/runs', params={'user_id': 'outsider-user', 'project_id': 'call-center'})
    assert outsider_response.status_code == 200
    assert outsider_response.json() == []


def test_product_projects_are_unique_per_user_and_project_key():
    first = client.post(
        '/api/product/projects',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'name': 'Call Center QA',
            'plan': 'starter',
        },
    )
    second = client.post(
        '/api/product/projects',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'name': 'Renamed Call Center',
            'plan': 'team',
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()['id'] == first.json()['id']
    assert second.json()['name'] == 'Renamed Call Center'
    assert second.json()['plan'] == 'team'

    with SessionLocal() as db:
        project_count = (
            db.query(ProductProject)
            .filter(ProductProject.user_id == 'demo-user', ProductProject.project_key == 'call-center')
            .count()
        )
        duplicate = ProductProject(user_id='demo-user', project_key='call-center', name='Duplicate', plan='free')
        db.add(duplicate)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError('Expected duplicate project key to violate unique constraint')

    assert project_count == 1


def test_project_list_reports_run_counts_per_project():
    for project_id, run_id in [('call-center', 'run-1'), ('call-center', 'run-2'), ('telehealth', 'run-3')]:
        response = client.post(
            '/api/product/runs',
            json={
                'user_id': 'demo-user',
                'project_id': project_id,
                'plan': 'starter',
                'report': {'run_id': run_id, 'overall_score': 90},
                'transcript': 'Agent: completed the task.',
            },
        )
        assert response.status_code == 200

    response = client.get('/api/product/projects', params={'user_id': 'demo-user'})

    assert response.status_code == 200
    counts = {project['project_id']: project['run_count'] for project in response.json()}
    assert counts == {'call-center': 2, 'telehealth': 1}


def test_saved_runs_can_filter_history_by_suite_and_scenario():
    runs = [
        ('run-1', 'call-center-voice-ai', 'billing-address-change', 82),
        ('run-2', 'call-center-voice-ai', 'angry-outage-escalation', 40),
        ('run-3', 'telehealth-agent', 'billing-address-change', 91),
    ]
    for run_id, suite_id, scenario_id, score in runs:
        response = client.post(
            '/api/product/runs',
            json={
                'user_id': 'demo-user',
                'project_id': 'call-center',
                'plan': 'starter',
                'report': {
                    'run_id': run_id,
                    'suite_id': suite_id,
                    'scenario_id': scenario_id,
                    'overall_score': score,
                },
                'transcript': 'Agent: completed the benchmark.',
            },
        )
        assert response.status_code == 200

    suite_response = client.get(
        '/api/product/runs',
        params={'user_id': 'demo-user', 'project_id': 'call-center', 'suite_id': 'call-center-voice-ai'},
    )
    scenario_response = client.get(
        '/api/product/runs',
        params={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'billing-address-change',
        },
    )

    assert suite_response.status_code == 200
    assert [run['report']['run_id'] for run in suite_response.json()] == ['run-2', 'run-1']
    assert scenario_response.status_code == 200
    assert [run['report']['run_id'] for run in scenario_response.json()] == ['run-1']


def test_saved_runs_include_project_regression_delta():
    first = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {'run_id': 'run-1', 'overall_score': 82},
            'transcript': 'Agent: completed most of the task.',
        },
    )
    second = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {'run_id': 'run-2', 'overall_score': 94},
            'transcript': 'Agent: completed the task.',
        },
    )

    assert first.status_code == 200
    assert first.json()['artifacts']['regression_delta'] == {
        'status': 'baseline',
        'previous_run_id': None,
        'previous_overall_score': None,
        'current_overall_score': 82,
        'score_delta': None,
    }
    assert second.status_code == 200
    assert second.json()['artifacts']['regression_delta'] == {
        'status': 'improved',
        'previous_run_id': 'run-1',
        'previous_overall_score': 82,
        'current_overall_score': 94,
        'score_delta': 12,
    }

    list_response = client.get('/api/product/runs', params={'user_id': 'demo-user', 'project_id': 'call-center'})
    assert list_response.json()[0]['artifacts']['regression_delta']['status'] == 'improved'


def test_saved_run_regression_delta_compares_same_scenario_when_labeled():
    runs = [
        ('run-1', 'billing-address-change', 82),
        ('run-2', 'angry-outage-escalation', 40),
        ('run-3', 'billing-address-change', 94),
    ]
    responses = []
    for run_id, scenario_id, score in runs:
        responses.append(
            client.post(
                '/api/product/runs',
                json={
                    'user_id': 'demo-user',
                    'project_id': 'call-center',
                    'plan': 'starter',
                    'report': {
                        'run_id': run_id,
                        'suite_id': 'call-center-voice-ai',
                        'scenario_id': scenario_id,
                        'overall_score': score,
                    },
                    'transcript': 'Agent: completed the benchmark.',
                },
            )
        )

    assert all(response.status_code == 200 for response in responses)
    assert responses[1].json()['artifacts']['regression_delta'] == {
        'status': 'baseline',
        'previous_run_id': None,
        'previous_overall_score': None,
        'current_overall_score': 40,
        'score_delta': None,
    }
    assert responses[2].json()['artifacts']['regression_delta'] == {
        'status': 'improved',
        'previous_run_id': 'run-1',
        'previous_overall_score': 82,
        'current_overall_score': 94,
        'score_delta': 12,
    }


def test_project_regression_summary_reports_latest_trend():
    for run_id, score in [('run-1', 82), ('run-2', 94), ('run-3', 88)]:
        response = client.post(
            '/api/product/runs',
            json={
                'user_id': 'demo-user',
                'project_id': 'call-center',
                'plan': 'starter',
                'report': {'run_id': run_id, 'overall_score': score},
                'transcript': 'Agent: completed the benchmark.',
            },
        )
        assert response.status_code == 200

    summary_response = client.get(
        '/api/product/projects/call-center/regression-summary',
        params={'user_id': 'demo-user'},
    )

    assert summary_response.status_code == 200
    assert summary_response.json() == {
        'user_id': 'demo-user',
        'project_id': 'call-center',
        'run_count': 3,
        'latest_run_id': 'run-3',
        'latest_score': 88,
        'previous_score': 94,
        'latest_delta': -6,
        'latest_status': 'regressed',
        'best_score': 94,
        'worst_score': 82,
        'average_score': 88.0,
        'passing_runs': 3,
        'failing_runs': 0,
        'pass_rate': 100.0,
        'scenario_summaries': [],
        'failure_category_summary': [],
    }


def test_project_regression_summary_reports_scenario_level_trends():
    runs = [
        ('run-1', 'billing-address-change', 82),
        ('run-2', 'angry-outage-escalation', 40),
        ('run-3', 'billing-address-change', 94),
    ]
    for run_id, scenario_id, score in runs:
        response = client.post(
            '/api/product/runs',
            json={
                'user_id': 'demo-user',
                'project_id': 'call-center',
                'plan': 'starter',
                'report': {
                    'run_id': run_id,
                    'suite_id': 'call-center-voice-ai',
                    'scenario_id': scenario_id,
                    'overall_score': score,
                },
                'transcript': 'Agent: completed the benchmark.',
            },
        )
        assert response.status_code == 200

    summary_response = client.get(
        '/api/product/projects/call-center/regression-summary',
        params={'user_id': 'demo-user'},
    )

    assert summary_response.status_code == 200
    payload = summary_response.json()
    assert payload['scenario_summaries'] == [
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'angry-outage-escalation',
            'run_count': 1,
            'latest_run_id': 'run-2',
            'latest_score': 40,
            'previous_score': None,
            'latest_delta': None,
            'latest_status': 'baseline',
            'passing_runs': 0,
            'failing_runs': 1,
            'pass_rate': 0.0,
        },
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'billing-address-change',
            'run_count': 2,
            'latest_run_id': 'run-3',
            'latest_score': 94,
            'previous_score': 82,
            'latest_delta': 12,
            'latest_status': 'improved',
            'passing_runs': 2,
            'failing_runs': 0,
            'pass_rate': 100.0,
        },
    ]
    assert payload['failure_category_summary'] == []



def test_project_regression_summary_can_focus_on_suite_and_scenario():
    runs = [
        ('run-1', 'call-center-voice-ai', 'billing-address-change', 82),
        ('run-2', 'call-center-voice-ai', 'angry-outage-escalation', 40),
        ('run-3', 'telehealth-agent', 'new-patient-triage', 91),
        ('run-4', 'call-center-voice-ai', 'billing-address-change', 94),
    ]
    for run_id, suite_id, scenario_id, score in runs:
        response = client.post(
            '/api/product/runs',
            json={
                'user_id': 'demo-user',
                'project_id': 'call-center',
                'plan': 'starter',
                'report': {
                    'run_id': run_id,
                    'suite_id': suite_id,
                    'scenario_id': scenario_id,
                    'overall_score': score,
                },
                'transcript': 'Agent: completed the benchmark.',
            },
        )
        assert response.status_code == 200

    suite_response = client.get(
        '/api/product/projects/call-center/regression-summary',
        params={'user_id': 'demo-user', 'suite_id': 'call-center-voice-ai'},
    )
    scenario_response = client.get(
        '/api/product/projects/call-center/regression-summary',
        params={
            'user_id': 'demo-user',
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'billing-address-change',
        },
    )

    assert suite_response.status_code == 200
    suite_summary = suite_response.json()
    assert suite_summary['run_count'] == 3
    assert suite_summary['latest_run_id'] == 'run-4'
    assert suite_summary['latest_score'] == 94
    assert {item['scenario_id'] for item in suite_summary['scenario_summaries']} == {
        'angry-outage-escalation',
        'billing-address-change',
    }

    assert scenario_response.status_code == 200
    scenario_summary = scenario_response.json()
    assert scenario_summary['run_count'] == 2
    assert scenario_summary['latest_run_id'] == 'run-4'
    assert scenario_summary['previous_score'] == 82
    assert scenario_summary['latest_delta'] == 12
    assert scenario_summary['scenario_summaries'] == [
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'billing-address-change',
            'run_count': 2,
            'latest_run_id': 'run-4',
            'latest_score': 94,
            'previous_score': 82,
            'latest_delta': 12,
            'latest_status': 'improved',
            'passing_runs': 2,
            'failing_runs': 0,
            'pass_rate': 100.0,
        }
    ]


def test_project_regression_summary_counts_verdict_failures():
    for run_id, score, verdict in [('run-1', 82, 'pass'), ('run-2', 94, 'needs_review'), ('run-3', 61, 'needs_review')]:
        response = client.post(
            '/api/product/runs',
            json={
                'user_id': 'demo-user',
                'project_id': 'call-center',
                'plan': 'starter',
                'report': {'run_id': run_id, 'overall_score': score, 'verdict': verdict},
                'transcript': 'Agent: completed the benchmark.',
            },
        )
        assert response.status_code == 200

    summary_response = client.get(
        '/api/product/projects/call-center/regression-summary',
        params={'user_id': 'demo-user'},
    )

    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary['passing_runs'] == 1
    assert summary['failing_runs'] == 2
    assert summary['pass_rate'] == 33.33
    assert summary['failure_category_summary'] == []


def test_project_regression_summary_reports_failure_category_mix():
    runs = [
        ('run-1', 82, ['tool_error', 'policy_miss']),
        ('run-2', 64, ['tool_error']),
        ('run-3', 91, ['handoff_miss']),
    ]
    for run_id, score, failure_categories in runs:
        response = client.post(
            '/api/product/runs',
            json={
                'user_id': 'demo-user',
                'project_id': 'call-center',
                'plan': 'starter',
                'report': {
                    'run_id': run_id,
                    'suite_id': 'call-center-voice-ai',
                    'scenario_id': 'angry-outage-escalation',
                    'overall_score': score,
                    'failure_categories': failure_categories,
                },
                'transcript': 'Agent: completed the benchmark.',
            },
        )
        assert response.status_code == 200

    summary_response = client.get(
        '/api/product/projects/call-center/regression-summary',
        params={'user_id': 'demo-user', 'suite_id': 'call-center-voice-ai'},
    )

    assert summary_response.status_code == 200
    assert summary_response.json()['failure_category_summary'] == [
        {'category': 'tool_error', 'count': 2, 'latest_run_id': 'run-2'},
        {'category': 'handoff_miss', 'count': 1, 'latest_run_id': 'run-3'},
        {'category': 'policy_miss', 'count': 1, 'latest_run_id': 'run-1'},
    ]


def test_project_regression_summary_rejects_missing_project():
    response = client.get(
        '/api/product/projects/missing/regression-summary',
        params={'user_id': 'demo-user'},
    )

    assert response.status_code == 404
    assert response.json()['detail'] == 'Project not found'


def test_saved_runs_preserve_run_metadata_in_history_and_export():
    response = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {
                'run_id': 'abc',
                'overall_score': 92,
                'run_metadata': {
                    'agent_version': 'agent-v12',
                    'prompt_version': 'prompt-2026-05-25',
                    'model_name': 'gpt-4.1-mini',
                    'notes': 'tightened escalation wording',
                },
            },
            'transcript': 'Agent: verified and completed the update.',
        },
    )

    assert response.status_code == 200
    saved = response.json()

    list_response = client.get('/api/product/runs', params={'user_id': 'demo-user', 'project_id': 'call-center'})
    assert list_response.json()[0]['report']['run_metadata']['prompt_version'] == 'prompt-2026-05-25'

    export_response = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'demo-user'})
    assert export_response.json()['report']['run_metadata']['model_name'] == 'gpt-4.1-mini'


def test_saved_runs_preserve_evidence_audit_summary_in_history_and_export():
    audit_summary = {
        'run_started_at': '2026-05-25T12:00:00+00:00',
        'evaluated_at': '2026-05-25T12:00:01+00:00',
        'input_artifact_types': ['transcript', 'action_trace', 'final_state'],
        'transcript_present': True,
        'action_trace_present': True,
        'final_state_present': True,
        'metadata_labels': ['agent_version', 'prompt_version'],
        'evaluator_version': 'assert-boundary',
        'export_readiness': {'ready': True, 'format': 'saved_run_json', 'missing': []},
    }
    report_contracts = {
        'suite_contract_manifest_sha256': 'a' * 64,
        'scenario_contract_sha256': 'b' * 64,
    }
    response = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {
                'run_id': 'abc',
                'overall_score': 92,
                **report_contracts,
                'evidence_audit_summary': audit_summary,
            },
            'transcript': 'Agent: verified and completed the update.',
        },
    )

    assert response.status_code == 200
    saved = response.json()

    list_response = client.get('/api/product/runs', params={'user_id': 'demo-user', 'project_id': 'call-center'})
    assert list_response.json()[0]['report']['evidence_audit_summary'] == audit_summary
    assert list_response.json()[0]['artifacts']['audit_artifacts'] == {
        'available': True,
        'ready_for_export': True,
        'artifact_types': ['transcript', 'action_trace', 'final_state'],
        'missing': [],
        'evaluator_version': 'assert-boundary',
        'classification': 'assert',
        'active_evaluator_input': True,
    }
    assert list_response.json()[0]['artifacts']['contract_artifacts'] == {
        'available': True,
        'suite_contract_manifest_sha256': 'a' * 64,
        'scenario_contract_sha256': 'b' * 64,
    }

    export_response = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'demo-user'})
    assert export_response.json()['report']['evidence_audit_summary'] == audit_summary
    assert export_response.json()['artifacts']['audit_artifacts']['ready_for_export'] is True
    assert export_response.json()['artifacts']['contract_artifacts'] == {
        'available': True,
        'suite_contract_manifest_sha256': 'a' * 64,
        'scenario_contract_sha256': 'b' * 64,
    }


def test_saved_runs_apply_assert_audit_policy_to_existing_artifacts():
    audit_summary = {
        'input_artifact_types': ['transcript'],
        'evaluator_version': 'assert-boundary',
        'export_readiness': {'ready': True, 'missing': []},
    }
    response = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {
                'run_id': 'assert-abc',
                'overall_score': 88,
                'evidence_audit_summary': audit_summary,
            },
            'transcript': 'Agent: verified and completed the update.',
        },
    )
    assert response.status_code == 200
    saved = response.json()

    db = SessionLocal()
    try:
        saved_run = db.query(ProductSavedRun).filter(ProductSavedRun.id == saved['id']).one()
        saved_run.artifact_json = json.dumps(
            {
                'audit_artifacts': {
                    'available': True,
                    'ready_for_export': True,
                    'artifact_types': ['transcript'],
                    'missing': [],
                    'evaluator_version': 'assert-boundary',
                }
            }
        )
        db.commit()
    finally:
        db.close()

    list_response = client.get('/api/product/runs', params={'user_id': 'demo-user', 'project_id': 'call-center'})
    list_audit = list_response.json()[0]['artifacts']['audit_artifacts']
    assert list_audit['classification'] == 'assert'
    assert list_audit['active_evaluator_input'] is True

    run_export = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'demo-user'})
    run_audit = run_export.json()['artifacts']['audit_artifacts']
    assert run_audit['classification'] == 'assert'
    assert run_audit['active_evaluator_input'] is True

    project_export = client.get('/api/product/projects/call-center/export', params={'user_id': 'demo-user'})
    project_audit = project_export.json()['runs'][0]['artifacts']['audit_artifacts']
    assert project_audit['classification'] == 'assert'
    assert project_audit['active_evaluator_input'] is True


def test_saved_runs_rebuild_missing_audit_artifacts_from_report():
    audit_summary = {
        'input_artifact_types': ['transcript', 'action_trace'],
        'evaluator_version': 'assert-boundary',
        'export_readiness': {'ready': True, 'missing': []},
    }
    response = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {
                'run_id': 'assert-missing-audit',
                'overall_score': 91,
                'evidence_audit_summary': audit_summary,
            },
            'transcript': 'Agent: verified and completed the update.',
        },
    )
    assert response.status_code == 200
    saved = response.json()

    db = SessionLocal()
    try:
        saved_run = db.query(ProductSavedRun).filter(ProductSavedRun.id == saved['id']).one()
        saved_run.artifact_json = json.dumps({'run_id': 'assert-missing-audit', 'overall_score': 91})
        db.commit()
    finally:
        db.close()

    list_response = client.get('/api/product/runs', params={'user_id': 'demo-user', 'project_id': 'call-center'})
    list_audit = list_response.json()[0]['artifacts']['audit_artifacts']
    assert list_audit == {
        'available': True,
        'ready_for_export': True,
        'artifact_types': ['transcript', 'action_trace'],
        'missing': [],
        'evaluator_version': 'assert-boundary',
        'classification': 'assert',
        'active_evaluator_input': True,
    }

    run_export = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'demo-user'})
    assert run_export.json()['artifacts']['audit_artifacts'] == list_audit

    project_export = client.get('/api/product/projects/call-center/export', params={'user_id': 'demo-user'})
    assert project_export.json()['runs'][0]['artifacts']['audit_artifacts'] == list_audit


def test_saved_runs_preserve_evidence_citations_in_history_and_export():
    citations = [
        {'source': 'transcript', 'kind': 'required_action', 'line_start': 1, 'line_end': 1, 'text': 'Agent: verified identity.'},
        {'source': 'action_trace', 'kind': 'required_action', 'index': 0, 'action': 'verify patient identity'},
        {'source': 'final_state', 'kind': 'final_state_assertion', 'path': 'complete', 'actual': True},
    ]
    response = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {
                'run_id': 'abc',
                'overall_score': 92,
                'evidence_citations': citations,
                'evidence_spans': citations,
            },
            'transcript': 'Agent: verified identity.',
        },
    )

    assert response.status_code == 200
    saved = response.json()
    assert saved['report']['evidence_citations'] == citations
    assert saved['artifacts']['evidence_citations'] == citations

    list_response = client.get('/api/product/runs', params={'user_id': 'demo-user', 'project_id': 'call-center'})
    assert list_response.json()[0]['artifacts']['evidence_citations'] == citations

    export_response = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'demo-user'})
    assert export_response.json()['report']['evidence_citations'] == citations
    assert export_response.json()['artifacts']['evidence_citations'] == citations


def test_saved_runs_include_vcon_export_summary_in_artifacts():
    response = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {
                'run_id': 'abc',
                'overall_score': 92,
                'vcon_export': {
                    'vcon': '0.0.1',
                    'dialog': [{'type': 'text', 'body': 'Agent: verified caller.'}],
                    'analysis': [{'type': 'agentic_benchmark_eval'}],
                    'source_format': 'benchmark',
                    'appended_analysis_type': 'agentic_benchmark_eval',
                },
            },
            'transcript': 'Agent: verified caller.',
        },
    )

    assert response.status_code == 200
    saved = response.json()
    assert saved['artifacts']['vcon_export'] == {
        'available': True,
        'dialog_turns': 1,
        'analysis_count': 1,
        'source_format': 'benchmark',
        'appended_analysis_type': 'agentic_benchmark_eval',
    }

    export_response = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'demo-user'})
    assert export_response.json()['artifacts']['vcon_export']['available'] is True


def test_saved_run_export_returns_owner_scoped_json_payload():
    create_response = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {'run_id': 'abc', 'overall_score': 92},
            'transcript': 'Agent: verified and completed the update.',
        },
    )
    saved = create_response.json()

    export_response = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'demo-user'})

    assert export_response.status_code == 200
    exported = export_response.json()
    assert exported['id'] == saved['id']
    assert exported['filename'] == f"agentbench-call-center-{saved['id']}.json"
    assert exported['project_name'] == 'Call Center'
    assert exported['firestore_path'] == f"users/demo-user/projects/call-center/runs/{saved['id']}"
    assert exported['report']['overall_score'] == 92
    assert exported['artifacts']['transcript_lines'] == 1
    assert exported['transcript'] == 'Agent: verified and completed the update.'

    wrong_owner = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'other-user'})
    assert wrong_owner.status_code == 404


def test_project_export_returns_owner_scoped_history_bundle():
    for run_id, score, scenario_id in [
        ('run-0', 70, 'billing-address-change'),
        ('run-1', 82, 'angry-outage-escalation'),
        ('run-2', 94, 'angry-outage-escalation'),
    ]:
        report = {
            'run_id': run_id,
            'overall_score': score,
            'suite_id': 'call-center-voice-ai',
            'scenario_id': scenario_id,
        }
        if run_id == 'run-2':
            report.update({
                'suite_contract_manifest_sha256': 'c' * 64,
                'scenario_contract_sha256': 'd' * 64,
                'vcon_export': {
                    'dialog': [{'type': 'text'}, {'type': 'text'}],
                    'analysis': [{'type': 'agentic_benchmark_eval'}],
                },
            })

        response = client.post(
            '/api/product/runs',
            json={
                'user_id': 'demo-user',
                'project_id': 'call-center',
                'plan': 'starter',
                'report': report,
                'transcript': f'Agent: completed benchmark {run_id}.',
            },
        )
        assert response.status_code == 200

    export_response = client.get('/api/product/projects/call-center/export', params={'user_id': 'demo-user'})

    assert export_response.status_code == 200
    exported = export_response.json()
    assert exported['filename'] == 'agentbench-call-center-project-export.json'
    assert exported['project_id'] == 'call-center'
    assert exported['project_name'] == 'Call Center'
    assert exported['firestore_collection_path'] == 'users/demo-user/projects/call-center/runs'
    assert exported['run_count'] == 3
    assert exported['summary']['latest_status'] == 'improved'
    assert exported['summary']['latest_score'] == 94
    assert exported['vcon_export_summary'] == {
        'available_records': 1,
        'missing_records': 2,
        'total_runs': 3,
        'dialog_turns': 2,
        'analysis_records': 1,
    }
    assert exported['contract_artifact_summary'] == {
        'available_records': 1,
        'missing_records': 2,
        'total_runs': 3,
        'suite_contract_manifest_sha256s': ['c' * 64],
        'scenario_contract_sha256s': ['d' * 64],
    }
    assert exported['scenario_coverage_summary'] == {
        'suite_id': None,
        'scenario_count': None,
        'covered_scenario_count': 2,
        'coverage_percent': None,
        'covered_scenario_ids': ['angry-outage-escalation', 'billing-address-change'],
        'missing_scenario_ids': [],
        'out_of_suite_scenario_ids': [],
        'covered_scenarios': [
            {'id': 'angry-outage-escalation', 'title': 'angry-outage-escalation'},
            {'id': 'billing-address-change', 'title': 'billing-address-change'},
        ],
        'missing_scenarios': [],
        'out_of_suite_scenarios': [],
        'recommended_next_scenario': None,
        'coverage_status': 'partial',
    }
    assert [run['report']['run_id'] for run in exported['runs']] == ['run-2', 'run-1', 'run-0']
    assert exported['runs'][0]['artifacts']['regression_delta']['status'] == 'improved'
    assert exported['exported_at']

    filtered_response = client.get(
        '/api/product/projects/call-center/export',
        params={
            'user_id': 'demo-user',
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'angry-outage-escalation',
        },
    )
    assert filtered_response.status_code == 200
    filtered = filtered_response.json()
    assert filtered['filename'] == 'agentbench-call-center-call-center-voice-ai-angry-outage-escalation-project-export.json'
    assert filtered['suite_id'] == 'call-center-voice-ai'
    assert filtered['scenario_id'] == 'angry-outage-escalation'
    assert filtered['run_count'] == 2
    assert filtered['summary']['run_count'] == 2
    assert filtered['summary']['latest_status'] == 'improved'
    assert filtered['vcon_export_summary']['total_runs'] == 2
    assert filtered['contract_artifact_summary'] == {
        'available_records': 1,
        'missing_records': 1,
        'total_runs': 2,
        'suite_contract_manifest_sha256s': ['c' * 64],
        'scenario_contract_sha256s': ['d' * 64],
    }
    assert filtered['scenario_coverage_summary'] == {
        'suite_id': 'call-center-voice-ai',
        'scenario_count': 4,
        'covered_scenario_count': 1,
        'coverage_percent': 25.0,
        'covered_scenario_ids': ['angry-outage-escalation'],
        'missing_scenario_ids': ['billing-address-change', 'interruption-correction-handling', 'refund-policy-boundary'],
        'out_of_suite_scenario_ids': [],
        'covered_scenarios': [{'id': 'angry-outage-escalation', 'title': 'Angry Outage Escalation'}],
        'missing_scenarios': [
            {'id': 'billing-address-change', 'title': 'Billing Address Change'},
            {'id': 'interruption-correction-handling', 'title': 'Interruption and Correction Handling'},
            {'id': 'refund-policy-boundary', 'title': 'Refund Policy Boundary'},
        ],
        'out_of_suite_scenarios': [],
        'recommended_next_scenario': {'id': 'billing-address-change', 'title': 'Billing Address Change'},
        'coverage_status': 'partial',
    }
    assert [run['report']['run_id'] for run in filtered['runs']] == ['run-2', 'run-1']

    wrong_owner = client.get('/api/product/projects/call-center/export', params={'user_id': 'other-user'})
    assert wrong_owner.status_code == 404


def test_llm_judge_is_gated_for_free_and_ready_for_paid_plans():
    free_response = client.post('/api/product/judge', json={'plan': 'free', 'report': {'overall_score': 82}})
    assert free_response.status_code == 200
    assert free_response.json()['status'] == 'blocked'
    assert free_response.json()['required_plan'] == 'starter'

    paid_response = client.post(
        '/api/product/judge',
        json={
            'plan': 'team',
            'report': {'evidence_spans': ['Verified customer identity', 'Created support ticket']},
            'transcript': 'Agent: verified customer identity.',
        },
    )
    assert paid_response.status_code == 200
    payload = paid_response.json()
    assert payload['status'] == 'ready'
    assert payload['credits'] == 10
    assert payload['evidence_citations'][:2] == ['Verified customer identity', 'Created support ticket']
    assert payload['spend_control'] == {
        'estimated_credits': 10,
        'daily_credit_limit': 200,
        'reserved_daily_credits': 0,
        'remaining_daily_credits': 200,
        'provider': 'vertex',
        'provider_configured': False,
        'within_budget': True,
    }


def test_llm_judge_spend_control_respects_budget_env(monkeypatch):
    monkeypatch.setenv('LLM_JUDGE_PROVIDER', 'openai')
    monkeypatch.setenv('LLM_JUDGE_API_KEY', 'test-key')
    monkeypatch.setenv('LLM_JUDGE_DAILY_CREDIT_LIMIT', '15')
    monkeypatch.setenv('LLM_JUDGE_RESERVED_DAILY_CREDITS', '8')

    response = client.post('/api/product/judge', json={'plan': 'starter', 'report': {'overall_score': 82}})

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'blocked'
    assert payload['message'] == 'LLM judge daily credit budget is exhausted. Increase the limit or wait for the next budget window.'
    assert payload['spend_control'] == {
        'estimated_credits': 10,
        'daily_credit_limit': 15,
        'reserved_daily_credits': 8,
        'remaining_daily_credits': 7,
        'provider': 'openai',
        'provider_configured': True,
        'within_budget': False,
    }



def test_product_audit_events_track_saved_runs_exports_and_judge_requests():
    saved = client.post(
        '/api/product/runs',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {
                'run_id': 'run-1',
                'suite_id': 'call-center-voice-ai',
                'scenario_id': 'billing-address-change',
                'overall_score': 91,
            },
            'transcript': 'Agent: verified the caller and updated the address.',
        },
    ).json()

    export_response = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'demo-user'})
    assert export_response.status_code == 200

    judge_response = client.post(
        '/api/product/judge',
        json={
            'user_id': 'demo-user',
            'project_id': 'call-center',
            'plan': 'starter',
            'report': {'overall_score': 91},
        },
    )
    assert judge_response.status_code == 200

    response = client.get('/api/product/audit-events', params={'user_id': 'demo-user', 'project_id': 'call-center'})

    assert response.status_code == 200
    events = response.json()
    assert [event['event_type'] for event in events] == ['judge.requested', 'run.exported', 'run.saved']
    assert events[0]['payload'] == {
        'project_id': 'call-center',
        'plan': 'starter',
        'status': 'ready',
        'credits': 10,
    }
    assert events[1]['payload'] == {'run_id': saved['id'], 'export_type': 'single_run'}
    assert events[2]['payload'] == {
        'run_id': saved['id'],
        'logical_run_id': 'run-1',
        'suite_id': 'call-center-voice-ai',
        'scenario_id': 'billing-address-change',
        'overall_score': 91,
    }

    filtered = client.get(
        '/api/product/audit-events',
        params={'user_id': 'demo-user', 'project_id': 'call-center', 'event_type': 'run.saved'},
    )
    assert [event['event_type'] for event in filtered.json()] == ['run.saved']

    outsider = client.get('/api/product/audit-events', params={'user_id': 'other-user', 'project_id': 'call-center'})
    assert outsider.status_code == 200
    assert outsider.json() == []


def test_project_export_preserves_custom_suite_covered_scenarios():
    project_response = client.post(
        "/api/product/projects",
        json={
            "user_id": "demo-user",
            "project_id": "custom-support",
            "name": "Custom Support QA",
            "plan": "starter",
        },
    )
    assert project_response.status_code == 200

    run_response = client.post(
        "/api/product/runs",
        json={
            "user_id": "demo-user",
            "project_id": "custom-support",
            "plan": "starter",
            "report": {
                "run_id": "custom-coverage-run",
                "overall_score": 88,
                "suite_id": "custom-suite",
                "scenario_id": "custom-refund-save",
            },
        },
    )
    assert run_response.status_code == 200

    export_response = client.get(
        "/api/product/projects/custom-support/export",
        params={"user_id": "demo-user", "suite_id": "custom-suite"},
    )

    assert export_response.status_code == 200
    assert export_response.json()["scenario_coverage_summary"] == {
        "suite_id": "custom-suite",
        "scenario_count": None,
        "covered_scenario_count": 1,
        "coverage_percent": None,
        "covered_scenario_ids": ["custom-refund-save"],
        "missing_scenario_ids": [],
        "out_of_suite_scenario_ids": [],
        "covered_scenarios": [{"id": "custom-refund-save", "title": "custom-refund-save"}],
        "missing_scenarios": [],
        "out_of_suite_scenarios": [],
        "recommended_next_scenario": None,
        "coverage_status": "partial",
    }


def test_project_export_tracks_out_of_suite_covered_scenarios_for_suite_filters():
    project_response = client.post(
        "/api/product/projects",
        json={
            "user_id": "demo-user",
            "project_id": "legacy-suite-history",
            "name": "Legacy Suite History",
            "plan": "starter",
        },
    )
    assert project_response.status_code == 200

    suite_runs = [
        ("legacy-suite-run", "angry-outage-escalation"),
        ("legacy-custom-run", "legacy-custom-scenario"),
    ]
    for run_id, scenario_id in suite_runs:
        run_response = client.post(
            "/api/product/runs",
            json={
                "user_id": "demo-user",
                "project_id": "legacy-suite-history",
                "plan": "starter",
                "report": {
                    "run_id": run_id,
                    "overall_score": 88,
                    "suite_id": "call-center-voice-ai",
                    "scenario_id": scenario_id,
                },
            },
        )
        assert run_response.status_code == 200

    export_response = client.get(
        "/api/product/projects/legacy-suite-history/export",
        params={"user_id": "demo-user", "suite_id": "call-center-voice-ai"},
    )

    assert export_response.status_code == 200
    assert export_response.json()["scenario_coverage_summary"] == {
        "suite_id": "call-center-voice-ai",
        "scenario_count": 4,
        "covered_scenario_count": 1,
        "coverage_percent": 25.0,
        "covered_scenario_ids": ["angry-outage-escalation"],
        "missing_scenario_ids": ["billing-address-change", "interruption-correction-handling", "refund-policy-boundary"],
        "out_of_suite_scenario_ids": ["legacy-custom-scenario"],
        "covered_scenarios": [{"id": "angry-outage-escalation", "title": "Angry Outage Escalation"}],
        "missing_scenarios": [
            {"id": "billing-address-change", "title": "Billing Address Change"},
            {"id": "interruption-correction-handling", "title": "Interruption and Correction Handling"},
            {"id": "refund-policy-boundary", "title": "Refund Policy Boundary"},
        ],
        "out_of_suite_scenarios": [{"id": "legacy-custom-scenario", "title": "legacy-custom-scenario"}],
        "recommended_next_scenario": {"id": "billing-address-change", "title": "Billing Address Change"},
        "coverage_status": "partial",
    }
