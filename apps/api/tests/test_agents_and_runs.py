from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services import acc_connection, agent_store, execution_run_store
from app.services.execution_runner import execute_execution_run, start_execution_run
from app.services.target_secrets import resolve_http_target_secret
from app.schemas.execution import ExecutionRunCreateRequest


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_agent_registry(tmp_path, monkeypatch):
    """Keep agent CRUD tests from reading or writing the local demo registry."""
    monkeypatch.setattr(agent_store, 'AGENTS_DIR', tmp_path / 'agents')
    execution_run_store.reset_execution_runs_for_tests()
    acc_connection.reset_acc_connections_for_tests()
    agent_store.reset_agents_for_tests(clear_files=True)
    yield
    execution_run_store.reset_execution_runs_for_tests()
    acc_connection.reset_acc_connections_for_tests()
    agent_store.reset_agents_for_tests(clear_files=True)


def test_agent_crud_round_trip():
    listed = client.get('/api/agents')
    assert listed.status_code == 200
    agents = listed.json()['agents']
    assert {item['id'] for item in agents} >= {'mock-text-agent', 'acc-voice-fixture-agent'}

    created = client.post(
        '/api/agents',
        json={
            'name': 'Custom support bot',
            'channel': 'text',
            'target': 'mock_agent',
            'description': 'Test agent',
        },
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()['id']

    detail = client.get(f'/api/agents/{agent_id}')
    assert detail.status_code == 200
    assert detail.json()['name'] == 'Custom support bot'

    patched = client.patch(f'/api/agents/{agent_id}', json={'name': 'Renamed bot'})
    assert patched.status_code == 200
    assert patched.json()['name'] == 'Renamed bot'

    deleted = client.delete(f'/api/agents/{agent_id}')
    assert deleted.status_code == 200
    assert client.get(f'/api/agents/{agent_id}').status_code == 404


def test_http_target_requires_real_connection_configuration():
    missing = client.post(
        '/api/agents',
        json={'name': 'No endpoint', 'channel': 'text', 'target': 'http_endpoint'},
    )
    assert missing.status_code == 422
    assert 'endpoint_url' in missing.text

    inline_credentials = client.post(
        '/api/agents',
        json={
            'name': 'Unsafe endpoint',
            'channel': 'text',
            'target': 'http_endpoint',
            'connection': {'endpoint_url': 'https://user:secret@example.test/chat'},
        },
    )
    assert inline_credentials.status_code == 422
    assert 'Do not embed credentials' in inline_credentials.text

    arbitrary_environment_variable = client.post(
        '/api/agents',
        json={
            'name': 'Unsafe credential reference',
            'channel': 'text',
            'target': 'http_endpoint',
            'connection': {
                'endpoint_url': 'https://example.test/chat',
                'auth_type': 'bearer_secret',
                'secret_ref': 'AWS_SECRET_ACCESS_KEY',
            },
        },
    )
    assert arbitrary_environment_variable.status_code == 422
    assert 'string_pattern_mismatch' in arbitrary_environment_variable.text


def test_http_target_credentials_only_resolve_from_dedicated_namespace(monkeypatch):
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'must-not-be-read')

    with pytest.raises(ValueError, match='credential is not configured'):
        resolve_http_target_secret('aws-secret-access-key')

    monkeypatch.setenv('CAE_HTTP_TARGET_SECRET_AWS_SECRET_ACCESS_KEY', 'approved-target-secret')
    assert resolve_http_target_secret('aws-secret-access-key') == 'approved-target-secret'


def test_http_target_executes_black_box_contract_and_persists_tester_provenance(monkeypatch):
    from app.services import execution_runner

    requests = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({'result': {'reply': 'Please confirm your ZIP code and phone number.'}}).encode()

    def fake_urlopen(request, *, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv('SUPPORT_AGENT_TOKEN', 'must-not-be-read')
    monkeypatch.setenv('CAE_HTTP_TARGET_SECRET_SUPPORT_AGENT_TOKEN', 'not-persisted')
    monkeypatch.setattr(execution_runner, 'urlopen', fake_urlopen)
    created = client.post(
        '/api/agents',
        json={
            'name': 'Staging HTTP support',
            'channel': 'text',
            'target': 'http_endpoint',
            'environment': 'staging',
            'connection': {
                'endpoint_url': 'https://support.example.test/chat',
                'auth_type': 'bearer_secret',
                'secret_ref': 'support-agent-token',
                'response_path': 'result.reply',
                'timeout_ms': 5000,
            },
        },
    )
    assert created.status_code == 200, created.text
    assert 'not-persisted' not in created.text

    payload = ExecutionRunCreateRequest(
        suite_id='call-center-voice-ai',
        scenario_ids=['billing-address-change'],
        agent_id=created.json()['id'],
        tester_id='scenario_simulator',
        executor_id='local_async_runner',
        user_id='agent-runs-user',
        project_id='agent-runs-project',
        evaluate=False,
    )
    queued = start_execution_run(payload)
    assert queued['tester_id'] == 'scenario_simulator'
    assert queued['executor_id'] == 'local_async_runner'
    assert queued['execution_snapshot']['agent']['target'] == 'http_endpoint'
    finished = execute_execution_run(queued['execution_run_id'], payload)

    assert finished['status'] == 'completed'
    conversation = finished['conversations'][0]
    assert conversation['turns'][1]['text'] == 'Please confirm your ZIP code and phone number.'
    provenance = conversation['final_state']['runtime_provenance']
    assert provenance['fixture_backed'] is False
    assert provenance['trace_visibility'] == 'black_box'
    assert provenance['tester_id'] == 'scenario_simulator'
    request, timeout = requests[0]
    assert timeout == 5
    assert request.headers['Authorization'] == 'Bearer not-persisted'
    posted = json.loads(request.data)
    assert posted['scenario']['id'] == 'billing-address-change'
    assert posted['history'][0]['role'] == 'user'
    assert posted['message'].startswith('Hi,')
    assert 'A busy customer who' not in posted['message']


def test_execution_rejects_tester_incompatible_with_mode():
    with pytest.raises(ValueError, match='text_callable mode requires tester_id=scenario_simulator'):
        ExecutionRunCreateRequest(mode='text_callable', tester_id='fixture_replay')
    with pytest.raises(ValueError, match='voice_fixture mode requires tester_id=fixture_replay'):
        ExecutionRunCreateRequest(mode='voice_fixture', tester_id='scenario_simulator')


def test_execution_with_agent_id_and_metrics_summary():
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            agent_id='mock-text-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )
    assert queued['agent_id'] == 'mock-text-agent'
    assert queued['mode'] == 'text_callable'

    finished = execute_execution_run(
        queued['execution_run_id'],
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            agent_id='mock-text-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        ),
    )
    assert finished['status'] in {'completed', 'needs_review'}
    conversation = finished['conversations'][0]
    assert conversation['metrics_summary'] is not None
    assert 'turn_count' in conversation['metrics_summary']
    assert isinstance(conversation.get('timeline'), list)
    assert finished.get('run_snapshot_path')

    execution_run_store.reset_execution_runs_for_tests()
    reloaded = execution_run_store.get_execution_run(queued['execution_run_id'])
    assert reloaded is not None
    assert reloaded['execution_run_id'] == queued['execution_run_id']
    assert reloaded['conversations'][0]['metrics_summary']['turn_count'] >= 1


def test_builtin_sample_voice_agent_run_uses_local_audio_loop_provenance():
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id='acc-voice-fixture-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )
    assert queued['mode'] == 'pipecat_webrtc'
    provenance = queued['provenance']
    assert provenance['target_kind'] == 'builtin_sample_voice'
    assert provenance['tester_id'] == 'pipecat_tester'
    assert provenance['executor_id'] == 'cae_local_audio_loop'
    assert provenance['evidence_source'] == 'saved_replay'
    assert provenance['live_external_connection'] is False
    assert provenance['saved_evidence'] is True
    assert provenance['synthetic_media'] is True
    assert provenance['honesty_label'] == (
        'Built-in sample agent · local audio-loop capture · saved fixture scoring and structured evidence · '
        'no phone or SIP call'
    )
    finished = execute_execution_run(
        queued['execution_run_id'],
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id='acc-voice-fixture-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        ),
    )
    assert finished['status'] in {'completed', 'needs_review', 'failed'}
    conversation = finished['conversations'][0]
    if conversation['status'] == 'completed':
        assert conversation['metrics_summary']['latency']['count'] >= 0
        assert isinstance(conversation['timeline'], list)


def test_saved_voice_agent_ignores_serialized_request_placeholders():
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id='acc-voice-fixture-agent',
            mode='text_callable',
            tester_id='scenario_simulator',
            executor_id='local_async_runner',
            audio_transport='none',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )

    assert queued['mode'] == 'pipecat_webrtc'
    assert queued['tester_id'] == 'pipecat_tester'
    assert queued['executor_id'] == 'cae_local_audio_loop'
    assert queued['execution_snapshot']['request']['audio_transport'] == 'pipecat_small_webrtc'


def test_acc_readiness_does_not_overclaim_cae_executor_availability(monkeypatch):
    blocked = client.post(
        '/api/agents',
        json={
            'name': 'SIP bot',
            'channel': 'voice',
            'target': 'sip_agent',
            'connection': {
                'sip_uri': 'sip:agent@example.com',
                'acc_base_url': 'http://acc.local:8026',
            },
        },
    )
    assert blocked.status_code in {400, 422}
    detail = blocked.json().get('detail')
    detail_text = detail if isinstance(detail, str) else str(detail)
    assert 'ACC' in detail_text

    status = client.get('/api/execution/acc-connection')
    assert status.status_code == 200
    assert status.json()['connected'] is False
    assert status.json()['label'] == 'Requires ACC connection'

    readiness_payload = {
        'ok': True,
        'route': '/api/pipecat-media-engine/readiness',
        'sharedEngineContract': {
            'requiredAdapters': [
                {'id': 'browser_webrtc', 'implementedNow': True},
                {
                    'id': 'sip_freeswitch_verto',
                    'implementedNow': True,
                    'liveMediaProofComplete': True,
                },
                {
                    'id': 'signalwire_sip_trunk',
                    'implementedNow': False,
                    'blocker': 'PSTN trunk routing is not ready.',
                },
            ],
        },
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return readiness_payload

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            assert url == 'http://acc.local:8026/api/pipecat-media-engine/readiness'
            return FakeResponse()

    monkeypatch.setattr(acc_connection.httpx, 'Client', FakeClient)
    tested = client.post(
        '/api/execution/acc-connection/test',
        json={'base_url': 'http://acc.local:8026'},
    )
    assert tested.status_code == 200, tested.text
    assert tested.json()['connected'] is True
    assert tested.json()['destinations']['sip_agent']['acc_ready'] is True
    assert tested.json()['destinations']['sip_agent']['cae_executor_available'] is False
    assert tested.json()['destinations']['sip_agent']['creatable'] is False
    assert tested.json()['destinations']['browser_webrtc_agent']['creatable'] is False
    assert tested.json()['destinations']['phone_agent']['creatable'] is False

    still_blocked = client.post(
        '/api/agents',
        json={
            'name': 'SIP bot',
            'channel': 'voice',
            'target': 'sip_agent',
            'connection': {
                'sip_uri': 'sip:agent@example.com:5060;transport=tcp',
                'acc_base_url': 'http://acc.local:8026',
            },
        },
    )
    assert still_blocked.status_code in {400, 422}
    assert 'execution adapter is not implemented' in still_blocked.text


def test_saved_voice_replay_is_not_a_creatable_target():
    response = client.post(
        '/api/agents',
        json={
            'name': 'Saved evidence is not a target',
            'channel': 'voice',
            'target': 'voice_fixture',
        },
    )
    assert response.status_code == 422
    assert 'evidence, not an agent target' in response.text


def test_rejects_incompatible_executor_for_builtin_sample_voice():
    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'agent_id': 'acc-voice-fixture-agent',
            'executor_id': 'acc_sip',
            'mode': 'pipecat_webrtc',
            'user_id': 'agent-runs-user',
            'project_id': 'agent-runs-project',
            'iterations': 1,
        },
    )
    assert response.status_code == 422
    assert 'cae_local_audio_loop' in response.text


def test_execution_persists_model_name_default_and_override():
    queued_default = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            agent_id='mock-text-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )
    assert queued_default['model_name'] == 'gpt-5.4'

    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            agent_id='mock-text-agent',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
            model_name='gpt-5.4-mini',
        )
    )
    assert queued['model_name'] == 'gpt-5.4-mini'

    via_api = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'agent_id': 'mock-text-agent',
            'user_id': 'agent-runs-user',
            'project_id': 'agent-runs-project',
            'model_name': 'gpt-4.1',
            'iterations': 1,
        },
    )
    assert via_api.status_code == 200, via_api.text
    assert via_api.json()['model_name'] == 'gpt-4.1'


def test_rejects_unsafe_agent_id_and_seed_mutations():
    bad = client.post(
        '/api/agents',
        json={'id': '../evil', 'name': 'Evil', 'channel': 'text', 'target': 'mock_agent'},
    )
    assert bad.status_code == 400

    seed_delete = client.delete('/api/agents/mock-text-agent')
    assert seed_delete.status_code == 400
    assert 'Seed agent' in seed_delete.json()['detail']

    seed_edit = client.patch('/api/agents/mock-text-agent', json={'target': 'offline_acc_fixture'})
    assert seed_edit.status_code == 422
    assert 'evidence, not an agent target' in seed_edit.text
    assert client.get('/api/agents/mock-text-agent').json()['target'] == 'mock_agent'


def test_rejects_null_patch_on_non_nullable_fields():
    created = client.post(
        '/api/agents',
        json={'name': 'Nullable guard', 'channel': 'text', 'target': 'mock_agent', 'description': 'keep'},
    )
    assert created.status_code == 200
    agent_id = created.json()['id']

    null_name = client.patch(f'/api/agents/{agent_id}', json={'name': None})
    assert null_name.status_code == 400

    cleared = client.patch(f'/api/agents/{agent_id}', json={'description': None})
    assert cleared.status_code == 200
    assert cleared.json()['description'] is None


@pytest.mark.parametrize(
    ('channel', 'target'),
    [
        ('text', 'voice_fixture'),
        ('voice', 'mock_agent'),
        ('voice', 'openai_codex'),
    ],
)
def test_rejects_incompatible_agent_channel_target_pairs(channel: str, target: str):
    created = client.post(
        '/api/agents',
        json={'name': 'Incompatible agent', 'channel': channel, 'target': target},
    )
    assert created.status_code == 422
    assert 'requires one of' in created.text

    valid = client.post(
        '/api/agents',
        json={'name': 'Text agent', 'channel': 'text', 'target': 'mock_agent'},
    )
    assert valid.status_code == 200

    patched = client.patch(
        f"/api/agents/{valid.json()['id']}",
        json={'target': 'voice_fixture'},
    )
    assert patched.status_code == 422
    assert 'evidence, not an agent target' in patched.text


def test_saved_text_replay_is_not_a_creatable_target():
    response = client.post(
        '/api/agents',
        json={
            'name': 'Saved text evidence is not a target',
            'channel': 'text',
            'target': 'offline_acc_fixture',
        },
    )
    assert response.status_code == 422
    assert 'evidence, not an agent target' in response.text


def test_agent_payload_rejects_execution_mode_that_bypasses_selected_text_target():
    from app.services.execution_runner import _resolve_agent_payload

    with pytest.raises(ValueError, match='not compatible with target'):
        _resolve_agent_payload(
            ExecutionRunCreateRequest(
                suite_id='call-center-voice-ai',
                scenario_ids=['cancellation-rescue'],
                agent_id='mock-text-agent',
                mode='pipecat_webrtc',
                user_id='agent-runs-user',
                project_id='agent-runs-project',
                iterations=1,
            )
        )


def test_voice_fixture_target_allows_pipecat_capture_proof_mode():
    from app.services.execution_runner import _resolve_agent_payload

    resolved = _resolve_agent_payload(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            agent_id='acc-voice-fixture-agent',
            mode='pipecat_webrtc',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
        )
    )

    assert resolved.mode == 'pipecat_webrtc'
    assert resolved.tester_id == 'pipecat_tester'


def test_explicit_text_mode_uses_selected_text_agent_target_when_callable_is_omitted():
    from app.services.execution_runner import _resolve_agent_payload

    created = client.post(
        '/api/agents',
        json={
            'name': 'Explicit live text target',
            'channel': 'text',
            'target': 'openai_codex',
        },
    )
    assert created.status_code == 200

    resolved = _resolve_agent_payload(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            agent_id=created.json()['id'],
            mode='text_callable',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
        )
    )

    assert resolved.mode == 'text_callable'
    assert resolved.text_callable == 'openai_codex'


def test_execution_rejects_callable_that_does_not_match_selected_target():
    created = client.post(
        '/api/agents',
        json={
            'name': 'HTTP provenance guard',
            'channel': 'text',
            'target': 'http_endpoint',
            'connection': {'endpoint_url': 'https://support.example.test/chat'},
        },
    )
    assert created.status_code == 200, created.text

    with pytest.raises(ValueError, match='would execute a different target'):
        start_execution_run(
            ExecutionRunCreateRequest(
                suite_id='call-center-voice-ai',
                scenario_ids=['billing-address-change'],
                agent_id=created.json()['id'],
                mode='text_callable',
                text_callable='mock_agent',
                user_id='agent-runs-user',
                project_id='agent-runs-project',
            )
        )


def test_rejects_openai_callable_without_agent_before_queueing(monkeypatch):
    created_records = []
    monkeypatch.setattr(
        execution_run_store,
        'create_execution_run',
        lambda record: created_records.append(record),
    )

    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['billing-address-change'],
            'mode': 'text_callable',
            'text_callable': 'openai_codex',
            'user_id': 'agent-runs-user',
            'project_id': 'agent-runs-project',
        },
    )

    assert response.status_code == 400
    assert response.json()['detail'] == 'openai_codex execution requires an agent_id.'
    assert created_records == []


def test_saved_text_replay_without_scenarios_defaults_to_cancellation_rescue():
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            text_callable='offline_acc_fixture',
            tester_id='scenario_simulator',
            executor_id='local_async_runner',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
        )
    )

    assert queued['mode'] == 'text_callable'
    assert queued['tester_id'] == 'fixture_replay'
    assert queued['executor_id'] == 'evidence_replay'
    assert queued['provenance']['saved_evidence'] is True
    assert queued['scenario_ids'] == ['cancellation-rescue']


@pytest.mark.parametrize(
    ('mode', 'target_kind'),
    [
        ('voice_fixture', 'saved_voice_replay'),
        ('pipecat_webrtc', 'builtin_sample_voice'),
    ],
)
def test_no_agent_voice_run_uses_inferred_target_provenance(mode: str, target_kind: str):
    queued = start_execution_run(
        ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['cancellation-rescue'],
            mode=mode,
            user_id='agent-runs-user',
            project_id='agent-runs-project',
        )
    )

    assert queued['provenance']['target_kind'] == target_kind
    assert queued['provenance']['target_channel'] == 'voice'


def test_explicit_openai_target_executes_selected_model_for_any_text_agent_without_fake_tool_evidence():
    from app.services.llm_providers import set_provider_for_tests

    class FakeOpenAIProvider:
        provider_id = 'openai'

        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.model_names: list[str | None] = []

        def status(self):
            return {'status': 'connected', 'provider': 'openai_codex'}

        def complete(self, prompt: str, *, model_name: str | None = None) -> str:
            self.prompts.append(prompt)
            self.model_names.append(model_name)
            return 'I can help update that. Please confirm your account email and ZIP code first.'

    created = client.post(
        '/api/agents',
        json={
            'name': 'Live OpenAI support agent',
            'channel': 'text',
            'target': 'openai_codex',
            'description': 'Verify the caller before account changes.',
        },
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()['id']
    fake = FakeOpenAIProvider()
    set_provider_for_tests('openai', fake)
    try:
        payload = ExecutionRunCreateRequest(
            suite_id='call-center-voice-ai',
            scenario_ids=['billing-address-change'],
            mode='text_callable',
            text_callable='openai_codex',
            agent_id=agent_id,
            model_name='gpt-5.4',
            user_id='agent-runs-user',
            project_id='agent-runs-project',
            iterations=1,
            evaluate=False,
        )
        queued = start_execution_run(payload)
        assert queued['mode'] == 'text_callable'
        # Queue-time settings remain executable even if a user removes the
        # registry entry before the background worker begins.
        deleted = client.delete(f'/api/agents/{agent_id}')
        assert deleted.status_code == 200
        finished = execute_execution_run(queued['execution_run_id'], payload)
    finally:
        set_provider_for_tests('openai', None)

    assert finished['status'] == 'completed'
    conversation = finished['conversations'][0]
    assert conversation['transcript'].endswith('Please confirm your account email and ZIP code first.')
    assert conversation['action_trace'] == []
    assert conversation['final_state']['complete'] is False
    assert conversation['final_state']['runtime_provenance']['fixture_backed'] is False
    assert conversation['final_state']['runtime_provenance']['live_tool_execution'] is False
    assert fake.model_names == ['gpt-5.4']
    assert 'Verify the caller before account changes.' in fake.prompts[0]
