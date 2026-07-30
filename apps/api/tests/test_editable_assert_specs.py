from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import yaml
from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.entities import EditableAssertSpecVersion, ProductProject, ProductWorkspace, ProductWorkspaceMember
from app.services.editable_assert_spec import EditableAssertSpec, save_spec
from app.services.llm_providers import set_provider_for_tests


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


def test_generate_calls_configured_llm_and_returns_draft_suggestions_that_require_user_approval(monkeypatch):
    monkeypatch.delenv('SPEC_GENERATION_MODEL', raising=False)
    monkeypatch.setenv('OPENAI_RESPONSES_MODEL', 'gpt-4.1-mini')

    class FakeProvider:
        def status(self):
            return {'status': 'connected', 'provider': 'fake-openai-oauth'}

        def complete(self, prompt, *, model_name=None):
            assert 'Cancellation rescue agent' in prompt
            assert 'Every severity must be exactly one of "info", "warning", or "error".' in prompt
            assert 'scenario_seeds is an array of plain strings, never objects.' in prompt
            assert 'judges must contain exactly one object' in prompt
            assert model_name == 'gpt-5.4-mini'
            return json.dumps({
                'required_behaviors': [{'id': 'diagnose', 'label': 'Diagnose reason', 'description': 'Ask why the caller wants to cancel.', 'severity': 'error'}],
                'forbidden_behaviors': [{'id': 'no-promises', 'label': 'No unsupported promises', 'description': 'Do not invent a discount.', 'severity': 'error'}],
                'scenario_seeds': ['Caller wants to cancel after a price increase.'],
                'scenarios': [
                    {'id': 'price', 'title': 'Price increase', 'persona': 'frustrated caller', 'description': 'Caller asks to cancel.', 'steps': ['Ask to cancel.', 'Explain price concern.'], 'expected_outcome': 'Safe save or handoff.'},
                    {'id': 'ineligible', 'title': 'Ineligible save', 'persona': 'direct caller', 'description': 'No eligible offer exists.', 'steps': ['Ask to cancel.'], 'expected_outcome': 'Cancellation proceeds without invented offer.'},
                ],
                'deterministic_checks': [{'id': 'final-state', 'label': 'Final state exists', 'description': 'Require terminal evidence.', 'severity': 'warning'}],
                'judges': [{'id': 'policy', 'name': 'Policy judge', 'kind': 'semantic', 'rubric': 'Score policy-safe resolution.', 'weight': 1, 'provider': 'configured-default', 'model': None}],
            })

    set_provider_for_tests('openai', FakeProvider())
    try:
        response = client.post(
            '/api/specs/generate',
            json={'title': 'Cancellation rescue agent', 'role': 'insurance retention voice agent', 'objective': 'Save eligible callers without making unauthorized billing promises.'},
        )
    finally:
        set_provider_for_tests('openai', None)

    assert response.status_code == 200
    payload = response.json()
    assert payload['provider'] == 'fake-openai-oauth'
    assert payload['status'] == 'draft'
    assert payload['requires_user_approval'] is True
    assert all(item['draft'] is True for item in payload['required_behaviors'])
    assert len(payload['scenarios']) == 2


def test_generate_uses_feature_specific_model_override(monkeypatch):
    monkeypatch.setenv('SPEC_GENERATION_MODEL', 'gpt-5.4')

    class FakeProvider:
        def status(self):
            return {'status': 'connected', 'provider': 'fake-openai-oauth'}

        def complete(self, prompt, *, model_name=None):
            assert model_name == 'gpt-5.4'
            return json.dumps({
                'required_behaviors': [],
                'forbidden_behaviors': [],
                'scenario_seeds': [],
                'scenarios': [],
                'deterministic_checks': [],
                'judges': [],
            })

    set_provider_for_tests('openai', FakeProvider())
    try:
        response = client.post(
            '/api/specs/generate',
            json={
                'title': 'Support agent',
                'role': 'customer support agent',
                'objective': 'Resolve account requests without unsupported claims.',
            },
        )
    finally:
        set_provider_for_tests('openai', None)

    assert response.status_code == 200
    assert response.json()['model'] == 'gpt-5.4'


def test_generate_fails_closed_when_no_llm_is_configured(monkeypatch):
    class DisconnectedProvider:
        def status(self):
            return {'status': 'disconnected'}

    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('LLM_JUDGE_API_KEY', raising=False)
    set_provider_for_tests('openai', DisconnectedProvider())
    try:
        response = client.post('/api/specs/generate', json={'title': 'Support agent', 'role': 'customer support agent', 'objective': 'Resolve account requests without unsupported claims.'})
    finally:
        set_provider_for_tests('openai', None)

    assert response.status_code == 503
    assert 'Connect OpenAI Codex OAuth' in response.json()['detail']


def test_generate_returns_client_safe_error_for_malformed_provider_content():
    class MalformedProvider:
        def status(self):
            return {'status': 'connected', 'provider': 'malformed-provider'}

        def complete(self, prompt, *, model_name=None):
            return 'not valid JSON'

    set_provider_for_tests('openai', MalformedProvider())
    try:
        response = client.post('/api/specs/generate', json={'title': 'Support agent', 'role': 'customer support agent', 'objective': 'Resolve account requests without unsupported claims.'})
    finally:
        set_provider_for_tests('openai', None)

    assert response.status_code == 502
    assert 'invalid editable ASSERT draft' in response.json()['detail']


def test_validate_reports_inline_errors_for_vague_or_empty_spec():
    response = client.post('/api/specs/validate', json={'spec': {'title': 'AI', 'role': '', 'objective': 'help'}})

    assert response.status_code == 200
    payload = response.json()
    assert payload['valid'] is False
    fields = {error['field'] for error in payload['errors']}
    assert {'title', 'role', 'objective', 'required_behaviors', 'forbidden_behaviors', 'scenarios'} <= fields


def test_preview_compiles_canonical_assert_yaml_and_validates_with_assert():
    response = client.post('/api/specs/preview', json={'spec': _valid_spec()})

    assert response.status_code == 200
    payload = response.json()
    assert payload['valid'] is True
    assert payload['assert_validator'] == 'assert-ai'
    assert payload['assert_validated'] is True
    parsed = yaml.safe_load(payload['yaml'])
    assert set(parsed) <= {'suite', 'run', 'behavior', 'context', 'default_model', 'artifacts_root', 'results_dir', 'pipeline'}
    assert parsed['behavior']['name'] == 'cancellation-rescue-agent'
    assert parsed['pipeline']['systematize'] == {}
    assert parsed['pipeline']['test_set']['scenario']['sample_size'] == 1
    assert parsed['pipeline']['judge']['dimensions']['semantic-policy-judge']['rubric'] == 'Check that the agent satisfies required behaviors and avoids forbidden behaviors.'
    assert '## Scenario examples' in parsed['behavior']['description']
    assert '## Deterministic checks' not in parsed['behavior']['description']
    assert 'Final state evidence present' not in parsed['behavior']['description']
    assert 'agentic_contact_center' not in parsed['context']
    assert 'runtime overrides' not in parsed['context'].lower()
    assert payload['export_filename'] == 'cancellation-rescue-agent.eval_config.yaml'
    assert {warning['field'] for warning in payload['warnings']} == {
        'extensions.agentic_contact_center',
        'deterministic_checks',
        'evidence_requirements',
    }


def test_save_without_id_preserves_the_suite_name_shown_in_preview():
    suffix = uuid4().hex
    spec = _valid_spec()
    spec.pop('id')
    preview = client.post('/api/specs/preview', json={'spec': spec})
    saved = client.post('/api/specs', json={'user_id': f'suite-user-{suffix}', 'project_id': f'suite-project-{suffix}', 'spec': spec})

    assert preview.status_code == 200
    assert saved.status_code == 200
    assert yaml.safe_load(preview.json()['yaml'])['suite'] == 'cancellation-rescue-agent'
    assert yaml.safe_load(saved.json()['yaml'])['suite'] == 'cancellation-rescue-agent'
    assert saved.json()['id'] == 'cancellation-rescue-agent'


def test_save_rejects_unapproved_generated_draft_and_versions_approved_spec_in_project_store():
    suffix = uuid4().hex
    user_id = f'spec-user-{suffix}'
    project_id = f'spec-project-{suffix}'
    draft = _valid_spec(
        generated_content_status='draft',
        required_behaviors=[{'id': 'generated-task', 'label': 'Completes task', 'description': 'Generated suggestion still needs approval.', 'draft': True}],
    )

    rejected = client.post('/api/specs', json={'user_id': user_id, 'project_id': project_id, 'spec': draft})

    assert rejected.status_code == 422
    assert 'Generated suggestions must be approved' in rejected.json()['detail']

    approved = _valid_spec(generated_content_status='approved')
    first = client.post('/api/specs', json={'user_id': user_id, 'project_id': project_id, 'spec': approved})
    second = client.post(
        '/api/specs/cancellation-rescue-agent/versions',
        json={'user_id': user_id, 'project_id': project_id, 'spec': {**approved, 'objective': 'Save eligible callers while documenting policy-safe evidence.'}},
    )

    assert first.status_code == 200
    assert first.json()['version'] == 1
    assert second.status_code == 200
    assert second.json()['version'] == 2
    exported = client.get('/api/specs/cancellation-rescue-agent/export', params={'user_id': user_id, 'project_id': project_id, 'format': 'yaml'})
    assert exported.status_code == 200
    assert 'Save eligible callers while documenting policy-safe evidence.' in exported.text
    with SessionLocal() as db:
        project = db.query(ProductProject).filter(ProductProject.user_id == user_id, ProductProject.project_key == project_id).one()
        versions = db.query(EditableAssertSpecVersion).filter(EditableAssertSpecVersion.project_id == project.id).all()
        assert sorted(item.version for item in versions) == [1, 2]


def test_saved_specs_are_scoped_by_owner_and_project():
    suffix = uuid4().hex
    first_user = f'owner-{suffix}'
    other_user = f'other-{suffix}'
    project_id = f'project-{suffix}'
    first = client.post('/api/specs', json={'user_id': first_user, 'project_id': project_id, 'spec': _valid_spec(objective='Save eligible callers while documenting owner scoped evidence.')})
    second = client.post('/api/specs', json={'user_id': other_user, 'project_id': project_id, 'spec': _valid_spec(objective='Save eligible callers while documenting other owner evidence.')})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()['version'] == 1
    assert second.json()['version'] == 1

    visible = client.get('/api/specs/cancellation-rescue-agent', params={'user_id': first_user, 'project_id': project_id})
    other_project = client.get('/api/specs/cancellation-rescue-agent', params={'user_id': first_user, 'project_id': 'other-project'})
    other_owner_export = client.get('/api/specs/cancellation-rescue-agent/export', params={'user_id': other_user, 'project_id': project_id, 'format': 'yaml'})

    assert visible.status_code == 200
    assert visible.json()['spec']['objective'] == 'Save eligible callers while documenting owner scoped evidence.'
    assert other_project.status_code == 404
    assert other_owner_export.status_code == 200
    assert 'other owner evidence' in other_owner_export.text
    assert 'owner scoped evidence' not in other_owner_export.text


def test_workspace_members_share_the_same_project_spec_history():
    suffix = uuid4().hex
    owner_id = f'workspace-owner-{suffix}'
    member_id = f'workspace-member-{suffix}'
    project_key = f'shared-project-{suffix}'
    with SessionLocal() as db:
        workspace = ProductWorkspace(owner_user_id=owner_id, workspace_key=f'workspace-{suffix}', name='Shared workspace')
        db.add(workspace)
        db.flush()
        db.add_all([
            ProductWorkspaceMember(workspace_id=workspace.id, user_id=owner_id, role='owner'),
            ProductWorkspaceMember(workspace_id=workspace.id, user_id=member_id, role='editor'),
        ])
        project = ProductProject(user_id=owner_id, workspace_id=workspace.id, project_key=project_key, name='Shared project')
        db.add(project)
        db.commit()

    first = client.post('/api/specs', json={'user_id': owner_id, 'project_id': project_key, 'spec': _valid_spec()})
    visible = client.get('/api/specs/cancellation-rescue-agent', params={'user_id': member_id, 'project_id': project_key})
    with SessionLocal() as db:
        db.add(ProductProject(user_id=member_id, project_key=project_key, name='Colliding personal project'))
        db.commit()
    second = client.post('/api/specs', json={'user_id': member_id, 'project_id': project_key, 'spec': _valid_spec(objective='Shared member edit remains in the workspace history.')})

    assert first.status_code == 200
    assert visible.status_code == 200
    assert second.status_code == 200
    assert second.json()['version'] == 2
    with SessionLocal() as db:
        projects = db.query(ProductProject).filter(ProductProject.project_key == project_key).all()
        assert len(projects) == 2
        shared_project = next(project for project in projects if project.workspace_id)
        personal_project = next(project for project in projects if not project.workspace_id)
        assert db.query(EditableAssertSpecVersion).filter(EditableAssertSpecVersion.project_id == shared_project.id).count() == 2
        assert db.query(EditableAssertSpecVersion).filter(EditableAssertSpecVersion.project_id == personal_project.id).count() == 0
        assert second.json()['project_id'] == shared_project.id


def test_workspace_viewers_can_read_but_cannot_write_shared_specs():
    suffix = uuid4().hex
    owner_id = f'viewer-owner-{suffix}'
    viewer_id = f'viewer-{suffix}'
    project_key = f'viewer-project-{suffix}'
    with SessionLocal() as db:
        workspace = ProductWorkspace(owner_user_id=owner_id, workspace_key=f'viewer-workspace-{suffix}', name='Viewer workspace')
        db.add(workspace)
        db.flush()
        db.add_all([
            ProductWorkspaceMember(workspace_id=workspace.id, user_id=owner_id, role='owner'),
            ProductWorkspaceMember(workspace_id=workspace.id, user_id=viewer_id, role='viewer'),
        ])
        db.add(ProductProject(user_id=owner_id, workspace_id=workspace.id, project_key=project_key, name='Viewer project'))
        db.commit()

    assert client.post('/api/specs', json={'user_id': owner_id, 'project_id': project_key, 'spec': _valid_spec()}).status_code == 200
    assert client.get('/api/specs/cancellation-rescue-agent', params={'user_id': viewer_id, 'project_id': project_key}).status_code == 200
    rejected = client.post('/api/specs', json={'user_id': viewer_id, 'project_id': project_key, 'spec': _valid_spec(objective='Viewer must not replace shared history.')})

    assert rejected.status_code == 422
    assert 'Workspace editor access' in rejected.json()['detail']


def test_same_key_projects_across_workspaces_require_unique_project_id():
    suffix = uuid4().hex
    member_id = f'multi-workspace-member-{suffix}'
    project_key = f'multi-workspace-project-{suffix}'
    project_ids = []
    owner_ids = []
    with SessionLocal() as db:
        for index in range(2):
            owner_id = f'multi-owner-{index}-{suffix}'
            owner_ids.append(owner_id)
            workspace = ProductWorkspace(owner_user_id=owner_id, workspace_key=f'multi-workspace-{index}-{suffix}', name=f'Workspace {index}')
            db.add(workspace)
            db.flush()
            db.add_all([
                ProductWorkspaceMember(workspace_id=workspace.id, user_id=owner_id, role='owner'),
                ProductWorkspaceMember(workspace_id=workspace.id, user_id=member_id, role='editor'),
            ])
            project = ProductProject(user_id=owner_id, workspace_id=workspace.id, project_key=project_key, name=f'Project {index}')
            db.add(project)
            db.flush()
            project_ids.append(project.id)
        db.commit()

    for owner_id, project_id in zip(owner_ids, project_ids, strict=True):
        assert client.post('/api/specs', json={'user_id': owner_id, 'project_id': project_id, 'spec': _valid_spec()}).status_code == 200

    ambiguous = client.get('/api/specs/cancellation-rescue-agent', params={'user_id': member_id, 'project_id': project_key})
    exact = client.get('/api/specs/cancellation-rescue-agent', params={'user_id': member_id, 'project_id': project_ids[1]})

    assert ambiguous.status_code == 409
    assert 'ambiguous across visible workspaces' in ambiguous.json()['detail']
    assert exact.status_code == 200


def test_save_rejects_spec_ids_that_cannot_be_addressed_as_one_path_segment():
    response = client.post('/api/specs', json={'user_id': 'slug-user', 'project_id': 'slug-project', 'spec': _valid_spec(id='support/v2')})

    assert response.status_code == 422
    assert 'path-safe slug' in response.json()['detail']


def test_json_export_uses_the_immutable_persisted_config(monkeypatch):
    suffix = uuid4().hex
    user_id = f'export-user-{suffix}'
    project_id = f'export-project-{suffix}'
    monkeypatch.setenv('ASSERT_DEFAULT_MODEL', 'openai/saved-model')
    saved = client.post('/api/specs', json={'user_id': user_id, 'project_id': project_id, 'spec': _valid_spec()})
    assert saved.status_code == 200
    monkeypatch.setenv('ASSERT_DEFAULT_MODEL', 'openai/new-environment-model')

    exported = client.get('/api/specs/cancellation-rescue-agent/export', params={'user_id': user_id, 'project_id': project_id, 'format': 'json'})

    assert exported.status_code == 200
    assert exported.json()['default_model']['name'] == 'openai/saved-model'


def test_preview_quotes_yaml_scalars_that_look_like_list_markers():
    spec = _valid_spec(
        required_behaviors=[{'id': 'dash-text', 'label': '- do not parse me as a list', 'description': '? keep pasted checklist text scalar'}],
        scenario_seeds=[': still scalar', '--- not a document marker'],
    )

    response = client.post('/api/specs/preview', json={'spec': spec})

    assert response.status_code == 200
    exported_yaml = response.json()['yaml']
    parsed = yaml.safe_load(exported_yaml)
    behavior = parsed['behavior']['description']
    assert '- do not parse me as a list' in behavior
    assert '? keep pasted checklist text scalar' in behavior
    assert ': still scalar' in behavior
    assert '--- not a document marker' in behavior


def test_preview_preserves_empty_yaml_mappings_as_objects():
    spec = _valid_spec(runtime_overrides={}, extensions={})

    response = client.post('/api/specs/preview', json={'spec': spec})

    assert response.status_code == 200
    exported_yaml = response.json()['yaml']
    parsed = yaml.safe_load(exported_yaml)
    assert parsed['pipeline']['systematize'] == {}


def test_preview_keeps_arbitrary_cae_metadata_out_of_assert_prompt_context():
    response = client.post('/api/specs/preview', json={'spec': _valid_spec(runtime_overrides={'foo: bar': {}}, extensions={'? odd': {'nested: key': {}}})})

    assert response.status_code == 200
    payload = response.json()
    parsed = yaml.safe_load(payload['yaml'])
    assert parsed['context'] == 'Target role: insurance retention voice agent'
    assert payload['normalized']['runtime_overrides'] == {'foo: bar': {}}
    assert payload['normalized']['extensions'] == {'? odd': {'nested: key': {}}}


def test_preview_reports_multiple_judges_as_an_inline_validation_error():
    judges = _valid_spec()['judges'] * 2

    response = client.post('/api/specs/preview', json={'spec': _valid_spec(judges=judges)})

    assert response.status_code == 200
    assert response.json()['valid'] is False
    assert any(error['field'] == 'judges' for error in response.json()['errors'])


def test_preview_reports_invalid_max_turns_without_raising_server_error():
    response = client.post('/api/specs/preview', json={'spec': _valid_spec(runtime_overrides={'target': {'endpoint': 'http://example.test'}, 'max_turns': 'many'})})

    assert response.status_code == 200
    assert response.json()['valid'] is False
    assert any(error['field'] == 'runtime_overrides.max_turns' for error in response.json()['errors'])


def test_preview_rejects_blank_checks_seeds_and_scenario_titles():
    response = client.post('/api/specs/preview', json={'spec': _valid_spec(
        required_behaviors=[{'label': '   '}],
        forbidden_behaviors=[{'label': '\t'}],
        scenario_seeds=['  '],
        scenarios=[{'title': ' ', 'description': 'No usable title.'}],
    )})

    assert response.status_code == 200
    assert response.json()['valid'] is False
    fields = {error['field'] for error in response.json()['errors']}
    assert {
        'required_behaviors.0.label',
        'forbidden_behaviors.0.label',
        'scenario_seeds.0',
        'scenarios.0.title',
    } <= fields


def test_atomic_version_allocation_keeps_both_concurrent_edits():
    suffix = uuid4().hex
    user_id = f'atomic-user-{suffix}'
    project_id = f'atomic-project-{suffix}'
    base = EditableAssertSpec.model_validate(_valid_spec())
    with SessionLocal() as db:
        assert save_spec(db=db, user_id=user_id, project_id=project_id, spec=base).version == 1

    def save_objective(objective: str) -> int:
        with SessionLocal() as db:
            return save_spec(db=db, user_id=user_id, project_id=project_id, spec=base.model_copy(update={'objective': objective})).version

    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = sorted(pool.map(save_objective, [
            'Save eligible callers while preserving the first concurrent edit.',
            'Save eligible callers while preserving the second concurrent edit.',
        ]))

    assert versions == [2, 3]
