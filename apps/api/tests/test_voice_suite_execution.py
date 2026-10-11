"""A voice suite queues bounded, separate calls without accepting judge proposals."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import pytest

# Register the application catalog before execution_runner binds its service
# functions, matching normal application startup and the existing route tests.
import app.main  # noqa: F401
from app.schemas.execution import ConversationTurn, ExecutionRunCreateRequest
from app.services import execution_run_store, execution_runner
from app.services.evaluation_contract import freeze_contract


@pytest.fixture(autouse=True)
def isolated_voice_suite(monkeypatch, tmp_path):
    monkeypatch.setattr(execution_runner, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(execution_run_store, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(execution_run_store, 'RUNS_DIR', tmp_path / 'artifacts' / 'execution-runs')
    monkeypatch.setattr(execution_runner, 'register_builtin_benchmark_extensions', lambda: None)
    suite = {
        'id': 'reviewed-voice-suite',
        'scenarios': [{'id': f'case-{index}', 'title': f'Reviewed case {index}'} for index in range(1, 22)],
        'optional_scenarios': [],
    }
    monkeypatch.setattr(execution_runner, 'get_suite', lambda _suite_id: deepcopy(suite))
    execution_run_store.reset_execution_runs_for_tests()
    yield
    execution_run_store.reset_execution_runs_for_tests()


def voice_request(**overrides):
    return ExecutionRunCreateRequest(
        **{
            'suite_id': 'reviewed-voice-suite',
            'scenario_ids': ['case-1', 'case-2', 'case-3'],
            'mode': 'pipecat_webrtc',
            'model_name': 'target-model',
            'tester_model_name': 'tester-model',
            'max_exchanges': 2,
            'duplex_timeout_seconds': 180,
            'user_id': 'voice-suite-owner',
            'project_id': 'voice-suite-project',
            **overrides,
        }
    )


@pytest.mark.parametrize(('overrides', 'message'), [
    ({'scenario_ids': []}, 'explicit scenario_ids'),
    ({'scenario_ids': [f'case-{index}' for index in range(1, 22)]}, '20 selected cases'),
    ({'iterations': 7}, '20 total conversations'),
    ({'concurrent_sessions': 2}, 'sequentially'),
])
def test_voice_limits_reject_before_runtime_preflight_and_queue(monkeypatch, overrides, message):
    def unexpected_side_effect(*_args, **_kwargs):
        pytest.fail('Invalid voice selection must not open or queue any voice work')

    monkeypatch.setattr(execution_runner, '_preflight_reference_runtime', unexpected_side_effect)
    monkeypatch.setattr(execution_run_store, 'create_execution_run', unexpected_side_effect)
    with pytest.raises(ValueError, match=message):
        execution_runner.start_execution_run(voice_request(**overrides), preflight=True)


def test_saved_voice_target_cannot_bypass_bounds_with_text_request_defaults(monkeypatch):
    agent = {'id': 'my-voice-agent', 'name': 'My voice agent', 'target': 'builtin_sample_voice'}
    monkeypatch.setattr(execution_runner, 'get_agent', lambda _agent_id: deepcopy(agent))
    with pytest.raises(ValueError, match='explicit scenario_ids'):
        execution_runner.start_execution_run(ExecutionRunCreateRequest(
            suite_id='reviewed-voice-suite', agent_id='my-voice-agent', model_name='target-model',
        ))


@pytest.mark.parametrize(('case_count', 'iterations'), [(20, 1), (1, 20), (10, 2)])
def test_voice_selection_accepts_exact_twenty_conversation_bound(case_count, iterations):
    selected = [f'case-{index}' for index in range(1, case_count + 1)]
    queued = execution_runner.start_execution_run(voice_request(scenario_ids=selected, iterations=iterations))
    assert queued['scenario_ids'] == selected
    assert queued['progress']['total_conversations'] == 20
    assert queued['execution_snapshot']['request']['concurrent_sessions'] == 1


def test_text_suite_authorization_and_existing_iteration_behavior_are_unchanged():
    queued = execution_runner.start_execution_run(ExecutionRunCreateRequest(
        suite_id='reviewed-voice-suite', mode='text_callable', iterations=2,
    ))
    assert len(queued['scenario_ids']) == 21
    assert queued['progress']['total_conversations'] == 42


def test_three_voice_cases_keep_separate_evidence_and_continue_after_failure(monkeypatch, tmp_path):
    agent = {
        'id': 'my-voice-agent', 'name': 'My configured voice agent', 'target': 'builtin_sample_voice',
        'metadata': {'model_name': 'saved-model'},
    }
    monkeypatch.setattr(execution_runner, 'get_agent', lambda _agent_id: deepcopy(agent))
    payload = voice_request(agent_id=agent['id'])
    queued = execution_runner.start_execution_run(payload)
    # Changing registry settings or a caller-provided execution payload after
    # queueing must not change the target or model for later cases.
    agent.update({'target': 'mock_agent', 'name': 'Changed after queueing'})
    calls = []
    contracts = {}

    async def fake_voice_call(**kwargs):
        scenario_id = kwargs['scenario_id']
        actual = kwargs['payload']
        calls.append((scenario_id, kwargs['conversation_id'], actual.model_dump()))
        assert actual.agent_id == 'my-voice-agent'
        assert actual.model_name == 'target-model'
        assert actual.tester_model_name == 'tester-model'
        assert actual.mode == 'pipecat_webrtc'
        assert actual.max_exchanges == 2
        assert actual.duplex_timeout_seconds == 180
        kwargs['event_observer']({'speaker': 'Caller', 'text': f'Opening {scenario_id}'})
        if scenario_id == 'case-2':
            raise RuntimeError('case-2 isolated media failure')
        contracts[scenario_id] = freeze_contract({'required_actions': [f'action-{scenario_id}']})
        return {
            'turns': [ConversationTurn(turn_index=1, speaker='caller', text=f'Opening {scenario_id}')],
            'transcript': f'Caller: Opening {scenario_id}',
            'recording': {'uri': f'memory://{scenario_id}.wav'},
            'audio_session': {'session_id': kwargs['conversation_id']},
            'verdict': 'needs_review',
            'evaluation_report': {'evaluation_contract_snapshot': contracts[scenario_id]},
        }

    monkeypatch.setattr(execution_runner, '_execute_pipecat_webrtc', fake_voice_call)
    changed = payload.model_copy(update={'model_name': 'changed-model', 'scenario_ids': ['case-1']})
    completed = execution_runner.execute_execution_run(queued['execution_run_id'], changed)
    assert [item[0] for item in calls] == ['case-1', 'case-2', 'case-3']
    assert len({item[1] for item in calls}) == 3
    assert completed['status'] == 'failed'
    assert completed['progress']['completed_conversations'] == 3
    assert completed['progress']['percent'] == 100
    first, failed, last = completed['conversations']
    assert [item['status'] for item in (first, failed, last)] == ['needs_review', 'failed', 'needs_review']
    assert 'case-2 isolated media failure' in failed['error']
    assert failed['live_events'][0]['text'] == 'Opening case-2'
    assert first['recording']['uri'] == 'memory://case-1.wav'
    assert last['recording']['uri'] == 'memory://case-3.wav'
    for item in (first, last):
        assert item['audio_session']['session_id'] == item['conversation_id']
        assert item['evaluation_findings']['evaluation_contract_snapshot'] == contracts[item['scenario_id']]
    assert all(not item['judge_reviews'] and item['evaluation_adjudication'] is None for item in completed['conversations'])
    rows = [json.loads(line) for line in (tmp_path / completed['inference_set_path']).read_text().splitlines()]
    assert [item['scenario_id'] for item in rows] == ['case-1', 'case-2', 'case-3']
    assert rows[1]['status'] == 'failed'
    assert rows[2]['evaluation_findings']['evaluation_contract_snapshot'] == contracts['case-3']


def test_voice_iterations_are_sequential_independent_sessions(monkeypatch):
    calls = []

    async def fake_voice_call(**kwargs):
        calls.append((kwargs['scenario_id'], kwargs['conversation_id']))
        return {'turns': [], 'verdict': 'needs_review'}

    monkeypatch.setattr(execution_runner, '_execute_pipecat_webrtc', fake_voice_call)
    payload = voice_request(iterations=2)
    queued = execution_runner.start_execution_run(payload)
    completed = execution_runner.execute_execution_run(queued['execution_run_id'], payload)
    assert [item[0] for item in calls] == ['case-1', 'case-2', 'case-3'] * 2
    assert len({item[1] for item in calls}) == 6
    assert completed['status'] == 'needs_review'
    assert [item['iteration'] for item in completed['conversations']] == [1, 1, 1, 2, 2, 2]


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_voice_transport_is_closed_when_a_case_raises(monkeypatch, cleanup_fails):
    events = []

    class FailingTransport:
        def __init__(self, **_kwargs):
            events.append('construct')

        async def connect(self, session_id, **_kwargs):
            events.append(('connect', session_id))

        async def start_recording(self, session_id):
            events.append(('record', session_id))

        async def run_duplex_session(self, session_id, **_kwargs):
            events.append(('call', session_id))
            raise RuntimeError('original media failure')

        async def disconnect(self, session_id, *, reason):
            events.append(('disconnect', session_id, reason))
            if cleanup_fails:
                raise RuntimeError('cleanup failure must not hide the call failure')

    monkeypatch.setattr(execution_runner, 'ReferencePipecatAgentTransport', FailingTransport)
    monkeypatch.setattr(execution_runner, 'ReferenceMediaServices', lambda _config: None)
    monkeypatch.setattr(execution_runner, 'resolve_reference_completion_provider', lambda _model: None)
    with pytest.raises(RuntimeError, match='original media failure'):
        asyncio.run(execution_runner._execute_pipecat_webrtc(
            execution_run_id='exec-test', conversation_id='exec-test-case-1-1',
            suite_id='reviewed-voice-suite', scenario_id='case-1', payload=voice_request(),
        ))
    assert events[-1] == ('disconnect', 'exec-test-case-1-1', 'runner_error')
