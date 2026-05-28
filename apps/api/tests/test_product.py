from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.db.database import SessionLocal
from app.main import app
from app.models.entities import ProductProject
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
    assert summary_response.json()['scenario_summaries'] == [
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
        'evaluator_version': 'deterministic-agentic-v1',
        'export_readiness': {'ready': True, 'format': 'saved_run_json', 'missing': []},
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
                'evidence_audit_summary': audit_summary,
            },
            'transcript': 'Agent: verified and completed the update.',
        },
    )

    assert response.status_code == 200
    saved = response.json()

    list_response = client.get('/api/product/runs', params={'user_id': 'demo-user', 'project_id': 'call-center'})
    assert list_response.json()[0]['report']['evidence_audit_summary'] == audit_summary

    export_response = client.get(f"/api/product/runs/{saved['id']}/export", params={'user_id': 'demo-user'})
    assert export_response.json()['report']['evidence_audit_summary'] == audit_summary


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
    for run_id, score in [('run-1', 82), ('run-2', 94)]:
        response = client.post(
            '/api/product/runs',
            json={
                'user_id': 'demo-user',
                'project_id': 'call-center',
                'plan': 'starter',
                'report': {'run_id': run_id, 'overall_score': score},
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
    assert exported['run_count'] == 2
    assert exported['summary']['latest_status'] == 'improved'
    assert exported['summary']['latest_score'] == 94
    assert [run['report']['run_id'] for run in exported['runs']] == ['run-2', 'run-1']
    assert exported['runs'][0]['artifacts']['regression_delta']['status'] == 'improved'
    assert exported['exported_at']

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
    assert response.json()['spend_control'] == {
        'estimated_credits': 10,
        'daily_credit_limit': 15,
        'reserved_daily_credits': 8,
        'remaining_daily_credits': 7,
        'provider': 'openai',
        'provider_configured': True,
        'within_budget': False,
    }
