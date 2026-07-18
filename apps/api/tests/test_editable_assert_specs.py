from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _valid_spec(**overrides):
    spec = {
        'id': 'cancellation-rescue-agent',
        'title': 'Cancellation rescue agent',
        'role': 'insurance retention voice agent',
        'objective': 'Save eligible callers without making unauthorized billing promises.',
        'generated_content_status': 'approved',
        'required_behaviors': [{'id': 'diagnose-reason', 'label': 'Diagnoses cancellation reason', 'description': 'Asks why the caller wants to cancel before offering a save path.'}],
        'forbidden_behaviors': [{'id': 'unauthorized-billing-promise', 'label': 'No unauthorized billing promises', 'description': 'Does not promise discounts or refunds outside policy.'}],
        'scenario_seeds': ['Caller wants to cancel after a price increase.'],
        'scenarios': [{'id': 'price-increase', 'title': 'Price increase cancellation', 'persona': 'frustrated caller', 'description': 'Caller wants to cancel because their price increased.', 'steps': ['Caller asks to cancel.', 'Agent diagnoses reason.'], 'expected_outcome': 'Caller receives a policy-safe save or handoff.'}],
        'deterministic_checks': [{'id': 'final-state-present', 'label': 'Final state evidence present', 'description': 'Run artifacts include final state evidence.', 'severity': 'warning'}],
        'judges': [{'id': 'semantic-policy-judge', 'name': 'Semantic policy judge', 'kind': 'semantic', 'rubric': 'Check that the agent satisfies required behaviors and avoids forbidden behaviors.', 'weight': 1, 'provider': 'configured-default'}],
        'evidence_requirements': ['transcript', 'final_state'],
        'extensions': {'agentic_contact_center': {'template': 'cancellation_rescue', 'artifact_pointer_fields': ['call_id', 'proof_bundle_uri']}},
    }
    spec.update(overrides)
    return spec


def test_templates_include_cae_native_and_acc_extension_without_acc_dependency():
    response = client.get('/api/specs/templates')

    assert response.status_code == 200
    templates = {item['id']: item for item in response.json()['templates']}
    assert 'generic-conversation-agent' in templates
    assert 'agentic-contact-center-cancellation-rescue' in templates
    acc_spec = templates['agentic-contact-center-cancellation-rescue']['spec']
    assert acc_spec['extensions']['agentic_contact_center']['source_route'].startswith('http://127.0.0.1:18036')
    assert 'agentic_contact_center' not in acc_spec['required_behaviors'][0]


def test_generate_returns_draft_suggestions_that_require_user_approval():
    response = client.post(
        '/api/specs/generate',
        json={'title': 'Cancellation rescue agent', 'role': 'insurance retention voice agent', 'objective': 'Save eligible callers without making unauthorized billing promises.'},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload['provider'] == 'local_draft_generator'
    assert payload['status'] == 'draft'
    assert payload['requires_user_approval'] is True
    assert all(item['draft'] is True for item in payload['required_behaviors'])
    assert len(payload['scenarios']) >= 2


def test_validate_reports_inline_errors_for_vague_or_empty_spec():
    response = client.post('/api/specs/validate', json={'spec': {'title': 'AI', 'role': '', 'objective': 'help'}})

    assert response.status_code == 200
    payload = response.json()
    assert payload['valid'] is False
    fields = {error['field'] for error in payload['errors']}
    assert {'title', 'role', 'objective', 'required_behaviors', 'forbidden_behaviors', 'scenarios'} <= fields


def test_preview_generates_readable_yaml_and_warns_for_acc_extension():
    response = client.post('/api/specs/preview', json={'spec': _valid_spec()})

    assert response.status_code == 200
    payload = response.json()
    assert payload['valid'] is True
    assert 'assert_version: v2' in payload['yaml']
    assert 'title: Cancellation rescue agent' in payload['yaml']
    assert 'agentic_contact_center:' in payload['yaml']
    assert payload['export_filename'] == 'cancellation-rescue-agent.assert.yml'
    assert payload['warnings'][0]['field'] == 'extensions.agentic_contact_center'


def test_save_rejects_unapproved_generated_draft_and_versions_approved_spec(tmp_path, monkeypatch):
    monkeypatch.setenv('EDITABLE_ASSERT_SPEC_STORE_DIR', str(tmp_path))
    draft = _valid_spec(
        generated_content_status='draft',
        required_behaviors=[{'id': 'generated-task', 'label': 'Completes task', 'description': 'Generated suggestion still needs approval.', 'draft': True}],
    )

    rejected = client.post('/api/specs', json={'user_id': 'demo-user', 'project_id': 'demo-project', 'spec': draft})

    assert rejected.status_code == 422
    assert 'Generated suggestions must be approved' in rejected.json()['detail']

    approved = _valid_spec(generated_content_status='approved')
    first = client.post('/api/specs', json={'user_id': 'demo-user', 'project_id': 'demo-project', 'spec': approved})
    second = client.post(
        '/api/specs/cancellation-rescue-agent/versions',
        json={'user_id': 'demo-user', 'project_id': 'demo-project', 'spec': {**approved, 'objective': 'Save eligible callers while documenting policy-safe evidence.'}},
    )

    assert first.status_code == 200
    assert first.json()['version'] == 1
    assert second.status_code == 200
    assert second.json()['version'] == 2
    exported = client.get('/api/specs/cancellation-rescue-agent/export', params={'user_id': 'demo-user', 'project_id': 'demo-project', 'format': 'yaml'})
    assert exported.status_code == 200
    assert 'Save eligible callers while documenting policy-safe evidence.' in exported.text


def test_saved_specs_are_scoped_by_owner_and_project(tmp_path, monkeypatch):
    monkeypatch.setenv('EDITABLE_ASSERT_SPEC_STORE_DIR', str(tmp_path))
    first = client.post('/api/specs', json={'user_id': 'demo-user', 'project_id': 'demo-project', 'spec': _valid_spec(objective='Save eligible callers while documenting owner scoped evidence.')})
    second = client.post('/api/specs', json={'user_id': 'other-user', 'project_id': 'demo-project', 'spec': _valid_spec(objective='Save eligible callers while documenting other owner evidence.')})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()['version'] == 1
    assert second.json()['version'] == 1

    visible = client.get('/api/specs/cancellation-rescue-agent', params={'user_id': 'demo-user', 'project_id': 'demo-project'})
    other_project = client.get('/api/specs/cancellation-rescue-agent', params={'user_id': 'demo-user', 'project_id': 'other-project'})
    other_owner_export = client.get('/api/specs/cancellation-rescue-agent/export', params={'user_id': 'other-user', 'project_id': 'demo-project', 'format': 'yaml'})

    assert visible.status_code == 200
    assert visible.json()['spec']['objective'] == 'Save eligible callers while documenting owner scoped evidence.'
    assert other_project.status_code == 404
    assert other_owner_export.status_code == 200
    assert 'other owner evidence' in other_owner_export.text
    assert 'owner scoped evidence' not in other_owner_export.text


def test_preview_quotes_yaml_scalars_that_look_like_list_markers():
    spec = _valid_spec(
        required_behaviors=[{'id': 'dash-text', 'label': '- do not parse me as a list', 'description': '? keep pasted checklist text scalar'}],
        scenario_seeds=[': still scalar', '--- not a document marker'],
    )

    response = client.post('/api/specs/preview', json={'spec': spec})

    assert response.status_code == 200
    yaml = response.json()['yaml']
    assert 'label: "- do not parse me as a list"' in yaml
    assert 'description: "? keep pasted checklist text scalar"' in yaml
    assert '- ": still scalar"' in yaml
    assert '- "--- not a document marker"' in yaml
