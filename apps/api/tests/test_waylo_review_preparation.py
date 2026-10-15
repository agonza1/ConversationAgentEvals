"""No provider calls: explicit legacy rule selection and frozen new-call context."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.db.database import get_db
from app.routes import assert_judge
from app.schemas.execution import ExecutionRunCreateRequest
from app.services import execution_run_store as store, execution_runner as runner, waylo_livekit, waylo_target, vcon_evidence
from app.services.evaluation_contract import freeze_contract, recorded_contract
from app.services.waylo_livekit import pcm_wav


@pytest.fixture
def capture(monkeypatch, tmp_path):
    row = {'conversation_id': 'conv', 'suite_id': 'waylo-mike-notes', 'scenario_id': 'mike-lookup-failure',
           'status': 'needs_review', 'verdict': 'needs_review', 'score': None,
           'turns': [{'turn_index': 1, 'speaker': 'Caller', 'text': 'Potatoes please.'}],
           'action_trace': [], 'final_state': {'request_list': []}, 'evaluation_findings': {}}
    run = {'execution_run_id': 'exec', 'user_id': 'owner', 'project_id': None,
           'executor_id': 'waylo_livekit', 'status': 'needs_review', 'conversations': [row]}
    monkeypatch.setattr(store, '_RUNS', {'exec': run})
    monkeypatch.setattr(store, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(store, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(assert_judge, 'execution_project_accessible', lambda **kw: True)
    app.dependency_overrides[get_db] = lambda: None
    yield run, row, TestClient(app), '/api/assert/runs/exec/conversations/conv'
    app.dependency_overrides.pop(get_db, None)


def test_legacy_contract_is_explicit_and_enables_manual_judge(capture, monkeypatch):
    run, row, client, path = capture
    before = deepcopy(run)
    # The original missing-contract guard remains, and no model runs.
    assert client.post(path + '/judge', json={'user_id': 'owner'}).status_code == 409
    draft = client.get(path + '/review-contract', params={'user_id': 'owner'}).json()
    assert run == before
    assert draft['source'] == 'current_scenario_not_historical'
    assert 'API_UNAVAILABLE' in draft['snapshot']['contract']['waylo_test']['setup']
    selected = client.post(path + '/review-contract', json={'user_id': 'owner', 'contract_sha256': draft['snapshot']['sha256']})
    assert selected.status_code == 200, selected.text
    assert recorded_contract(row)['_snapshot_sha256'] == draft['snapshot']['sha256']
    assert row['turns'] == before['conversations'][0]['turns']
    assert row['verdict'] == 'needs_review' and row['score'] is None
    assert row['evaluation_findings']['contract_selection']['source'] == 'operator_selected_current_scenario'
    calls = []
    monkeypatch.setattr(assert_judge, '_resolve_model', lambda value: 'fixture-model')
    monkeypatch.setattr(assert_judge.assert_judge_requests, 'claim', lambda *a, **kw: ('fixture-invocation', None))
    monkeypatch.setattr(assert_judge.assert_judge_requests, 'retain', lambda *a, **kw: None)
    monkeypatch.setattr(assert_judge, 'record_judge_request', lambda **kw: None)
    def judge(**kw):
        calls.append(kw)
        return {'status': 'ready', 'judge_result': {'agrees': True}, 'evidence_citations': []}
    monkeypatch.setattr(assert_judge, 'run_upstream_assert_judge', judge)
    response = client.post(path + '/judge', json={'user_id': 'owner'})
    assert response.status_code == 200, response.text
    assert len(calls) == 1 and calls[0]['scenario_contract']['id'] == 'mike-lookup-failure'
    assert row['verdict'] == 'needs_review'  # Review still requires acceptance.


@pytest.mark.parametrize('change', ['wrong_owner', 'wrong_hash', 'running', 'existing_review', 'existing_contract', 'graded'])
def test_preparation_refuses_unsafe_changes_without_mutation(capture, change):
    run, row, client, path = capture
    draft = client.get(path + '/review-contract', params={'user_id': 'owner'}).json()['snapshot']
    payload = {'user_id': 'owner', 'contract_sha256': draft['sha256']}
    if change == 'wrong_owner': payload['user_id'] = 'other'
    if change == 'wrong_hash': payload['contract_sha256'] = '0' * 64
    if change == 'running': run['status'] = 'running'
    if change == 'existing_review': row['judge_reviews'] = [{'review_id': 'retained'}]
    if change == 'existing_contract': row['evaluation_findings']['evaluation_contract_snapshot'] = draft
    if change == 'graded': row['score'] = 70
    before = deepcopy(run)
    assert client.post(path + '/review-contract', json=payload).status_code in {404, 409}
    assert run == before


def test_selected_rules_hash_detects_catalog_changes(capture, monkeypatch):
    run, row, client, path = capture
    draft = client.get(path + '/review-contract', params={'user_id': 'owner'}).json()['snapshot']
    monkeypatch.setattr(runner, '_scenario_definition', lambda *a: {'id': 'mike-lookup-failure', 'goal': 'Changed rules'})
    assert client.post(path + '/review-contract', json={'user_id': 'owner', 'contract_sha256': draft['sha256']}).status_code == 409
    assert row['evaluation_findings'] == {}


@pytest.mark.parametrize('noise', [None, {'snr_db': 20, 'seed': 7}])
def test_noise_changes_actual_sent_audio_with_retained_seed(monkeypatch, noise):
    source = pcm_wav(b'\x00\x10' * 2400, 24000)
    scenario = {'id': 'case', 'goal': 'Information only', 'caller_steps': ['Exact opener']}
    frozen = freeze_contract(scenario, suite_id='suite', scenario_id='case')
    monkeypatch.setattr(runner, '_scenario_definition', lambda *a: pytest.fail('Must use frozen rules'))
    monkeypatch.setattr(runner, 'ReferenceMediaServices', lambda *a: SimpleNamespace(
        client=SimpleNamespace(close=lambda: None), synthesize=lambda *a, **kw: source))
    monkeypatch.setattr(waylo_target, 'WayloClient', lambda *a, **kw: object())
    monkeypatch.setattr(vcon_evidence, 'decode_evidence', lambda *a: {'tool_events': [], 'voice_events': [], 'context': {}, 'state_snapshots': []})
    async def call(**kw):
        assert await kw['next_utterance']([], 1) == 'Exact opener'
        audio, metadata = await kw['synthesize']('Exact opener', 1)
        if noise:
            assert audio != source and metadata['seed'] == 8
            assert metadata['snr_db_requested'] == 20
            assert (await kw['synthesize']('Exact opener', 1))[0] == audio
        else:
            assert audio == source
        return {'ietf_vcon_export': {}, 'final_state': {}}
    monkeypatch.setattr(waylo_livekit, 'run_waylo_call', call)
    payload = ExecutionRunCreateRequest(mode='pipecat_webrtc', executor_id='waylo_livekit', max_exchanges=1, tester_noise=noise)
    asyncio.run(runner._execute_waylo(execution_run_id='exec', conversation_id='conv', suite_id='suite',
        scenario_id='case', payload=payload, agent_snapshot={'connection': {'auth_type': 'none'}},
        event_observer=None, scenario_snapshot=frozen))


@pytest.mark.parametrize('value', [{'snr_db': float('inf')}, {'snr_db': 41}, {'seed': -1}, {'seed': 4294967296}])
def test_bad_noise_fails_closed(value):
    with pytest.raises(ValidationError):
        ExecutionRunCreateRequest(mode='pipecat_webrtc', executor_id='waylo_livekit', tester_noise=value)


def test_noise_rejected_for_unsupported_transport():
    with pytest.raises(ValidationError, match='only for Waylo'):
        ExecutionRunCreateRequest(tester_noise={'seed': 1})


def test_completed_capture_retains_launch_contract_without_grading(capture, monkeypatch):
    run, row, client, path = capture
    frozen = freeze_contract({'id': 'case', 'title': 'Frozen case', 'goal': 'Keep notes only'})
    run['execution_snapshot'] = {'evaluation_contracts': {'case': frozen}}
    monkeypatch.setattr(runner, 'get_suite', lambda *a: {'scenarios': [{'id': 'case', 'title': 'Frozen case'}]})
    async def execute(**kw):
        assert kw['scenario_snapshot'] == frozen
        return {'turns': [], 'verdict': 'needs_review', 'score': None,
                'ietf_vcon_export': {'vcon': '0.4.0', 'dialog': [], 'uuid': 'fixture'}}
    monkeypatch.setattr(runner, '_execute_waylo', execute)
    payload = ExecutionRunCreateRequest(mode='pipecat_webrtc', executor_id='waylo_livekit', max_exchanges=1)
    result = runner._run_one_conversation(execution_run_id='exec', suite_id='suite',
        scenario_id='case', iteration=1, payload=payload)
    retained = recorded_contract(result.model_dump())
    assert retained['goal'] == 'Keep notes only'
    assert result.score is None and result.verdict == 'needs_review'
    assert result.evaluation_findings['scoring_mode'] == 'capture_only'


def test_queue_freezes_rules_and_generated_noise_seed(capture, monkeypatch):
    scenario = {'id': 'case', 'title': 'Original rules', 'caller_steps': ['Original opener'], 'goal': 'Notes only'}
    target = {'id': 'target', 'name': 'Fixture', 'target': 'waylo', 'channel': 'voice',
              'connection': {'auth_type': 'bearer_secret', 'secret_ref': 'fixture-only', 'endpoint_url': 'https://example.test'}}
    monkeypatch.setattr(runner, '_resolve_agent_payload', lambda value: value)
    monkeypatch.setattr(runner, 'get_agent', lambda *a: target)
    monkeypatch.setattr(runner, 'get_suite', lambda *a: {'id': 'suite', 'scenarios': [scenario]})
    monkeypatch.setattr(runner, 'resolve_http_target_secret', lambda *a: 'not-a-real-secret')
    payload = ExecutionRunCreateRequest(agent_id='target', suite_id='suite', scenario_ids=['case'],
        mode='pipecat_webrtc', executor_id='waylo_livekit', tester_noise={'snr_db': 20})
    queued = runner.start_execution_run(payload)
    frozen = queued['execution_snapshot']['evaluation_contracts']['case']
    scenario['goal'] = 'Changed after queueing'
    assert recorded_contract({'evaluation_contract_snapshot': frozen})['goal'] == 'Notes only'
    assert queued['execution_snapshot']['request']['tester_noise']['seed'] == payload.tester_noise.seed
    assert frozen['contract']['waylo_test']['condition'] == 'seeded_white_noise'
    assert payload.evaluate is False
