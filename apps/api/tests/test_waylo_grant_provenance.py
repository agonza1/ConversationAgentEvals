"""No live provider calls: grant bounds and the caller that actually produced evidence."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.main import app  # Initialize catalog extensions before capturing service aliases.
from app.schemas.execution import ConversationRecord, ExecutionRunCreateRequest
from app.services import execution_runner as runner, execution_run_store as runs, waylo_livekit
from app.services.assert_transcript_adapter import build_assert_inference_row
from app.services.reference_model_preflight import prepare_execution_reference_models
from app.services.waylo_target import project_capture
from test_waylo_session_execution import connected, TARGET, HEADERS, run_payload
from test_waylo_target import captured


SUITE = {'id': 'suite', 'scenarios': [
    {'id': 'fixed', 'title': 'Fixed', 'caller_steps': ['Exact opener.', 'Exact follow-up.']},
    {'id': 'adaptive', 'title': 'Adaptive', 'user_goal': 'Ask a question.'},
]}


@pytest.mark.parametrize('selection,extra,expected', [
    (['fixed'], {}, None),
    (['adaptive'], {'max_exchanges': 1}, None),
    (['adaptive'], {'audio_plan_path': 'fixture.json'}, None),
    (['adaptive'], {}, 'gpt-6-luna'),
    (['fixed', 'adaptive'], {}, 'gpt-6-luna'),
])
def test_unused_explicit_tester_model_is_cleared_and_adaptive_selection_preserved(monkeypatch, selection, extra, expected):
    monkeypatch.setattr(runner, 'get_agent', lambda _: TARGET)
    monkeypatch.setattr(runner, 'get_suite', lambda _: SUITE)
    request = ExecutionRunCreateRequest(agent_id=TARGET['id'], suite_id='suite',
        scenario_ids=selection, tester_model_name='gpt-6-luna', **extra)
    resolved = runner._resolve_agent_payload(request)
    assert resolved.tester_model_name == expected
    assert prepare_execution_reference_models(resolved).tester_model_name == expected


def test_scripted_full_suite_does_not_default_to_a_model(monkeypatch):
    monkeypatch.setattr(runner, 'get_agent', lambda _: TARGET)
    monkeypatch.setattr(runner, 'get_suite', lambda _: {'scenarios': [SUITE['scenarios'][0]]})
    monkeypatch.setattr(runner, 'default_reference_model_name', lambda: pytest.fail('No caller model is used'))
    assert runner._resolve_agent_payload(ExecutionRunCreateRequest(agent_id=TARGET['id'], suite_id='suite')).tester_model_name is None


def test_ten_case_browser_suite_is_rejected_at_minimum_timeout_before_queueing(connected, monkeypatch):
    _, proof, client = connected
    monkeypatch.setattr(runner, 'get_suite', lambda _: {'id': 'suite', 'scenarios': [
        {'id': f'case-{index}', 'caller_steps': ['Exact caller.']} for index in range(10)]})
    response = client.post('/api/execution/runs', json=run_payload(suite_id='suite',
        scenario_ids=[f'case-{index}' for index in range(10)], duplex_timeout_seconds=30), headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == 400 and 'expires' in response.text
    assert runs.list_execution_runs(user_id='execution-user') == []


def test_small_suite_queues_without_presumed_model_and_mixed_exports_use_each_caller(connected, monkeypatch):
    _, proof, client = connected
    monkeypatch.setattr(runner, 'get_suite', lambda _: SUITE)
    monkeypatch.setattr('app.routes.execution.execute_execution_run', lambda *_: None)
    response = client.post('/api/execution/runs', json=run_payload(suite_id='suite', scenario_ids=['fixed', 'adaptive'],
        tester_model_name='gpt-6-luna'), headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run['tester_model_name'] is None
    records = []
    for case, source, model in [('adaptive', 'adaptive_llm', 'gpt-6-luna'), ('fixed', 'scripted_caller', None)]:
        conversation = ConversationRecord(conversation_id=case, execution_run_id=run['execution_run_id'],
            suite_id='suite', scenario_id=case, mode='pipecat_webrtc', status='completed',
            tester_source=source, tester_model_name=model, turns=[{'turn_index': 1, 'speaker': 'Caller', 'text': 'Hello.'}])
        run = runs.upsert_conversation(run['execution_run_id'], conversation)
        records.append(conversation.model_dump(mode='json'))
    assert run['tester_model_name'] == 'gpt-6-luna'
    assert build_assert_inference_row(run=run, conversation=records[0])['tester_model'] == 'gpt-6-luna'
    assert build_assert_inference_row(run=run, conversation=records[1])['tester_model'] == 'scripted_caller'
    assert records[1]['tester_model_name'] is None


@pytest.mark.parametrize('case,cap,fixture,source,invocations,fail_capture', [
    ('fixed', 2, False, 'scripted_caller', 0, False),
    ('adaptive', 1, False, 'scenario_opener', 0, False),
    ('adaptive', 2, True, 'controlled_audio_fixture', 0, False),
    ('adaptive', 2, False, 'adaptive_llm', 1, False),
    ('adaptive', 2, False, 'execution_failed', 1, True),
])
def test_actual_completion_not_configuration_determines_caller_provenance(tmp_path, monkeypatch, case, cap, fixture, source, invocations, fail_capture):
    calls = []
    class Provider:
        def complete(self, prompt, *, model_name):
            calls.append(model_name)
            return 'Adaptive follow-up.'
    monkeypatch.setattr(runner, 'get_suite', lambda _: SUITE)
    monkeypatch.setattr(runner, 'resolve_reference_completion_provider', lambda _: Provider())
    monkeypatch.setattr(runner, 'ReferenceMediaServices', lambda _: SimpleNamespace(client=SimpleNamespace(close=lambda: None)))
    target = {**TARGET, 'connection': {**TARGET['connection'], 'auth_type': 'bearer_secret', 'secret_ref': 'synthetic-test'}}
    monkeypatch.setattr('app.services.waylo_target.WayloClient', lambda *args, **kwargs: None)
    if fixture:
        (tmp_path / 'audio.wav').write_bytes(waylo_livekit.pcm_wav(b'\x01\x00' * 100))
        (tmp_path / 'fixture.json').write_text(json.dumps({'scenario_id': case, 'seed': 1,
            'fixtures': [{'fixture_id': 'audio', 'uri': 'audio.wav', 'expected_caller_act': 'ask', 'metadata': {'reference_text': 'Recorded fixture.'}}],
            'steps': [{'step_id': 'first', 'fixture_id': 'audio', 'expected_caller_act': 'ask'}]}))
        monkeypatch.setattr(runner, '_repo_path', lambda path: tmp_path / path)
    async def fake_call(**kwargs):
        await kwargs['next_utterance']([], 1)
        if cap > 1 and not fixture:
            await kwargs['next_utterance']([], 2)
        if fail_capture:
            raise RuntimeError('Capture failed after adaptive completion.')
        return {'ietf_vcon_export': project_capture(captured(origin='cae_ai_tester'))['vcon'], 'final_state': {}}
    monkeypatch.setattr(waylo_livekit, 'run_waylo_call', fake_call)
    payload = ExecutionRunCreateRequest(agent_id=TARGET['id'], mode='pipecat_webrtc', executor_id='waylo_livekit',
        tester_model_name='gpt-6-luna', max_exchanges=cap, audio_plan_path='fixture.json' if fixture else None)
    if fail_capture:
        monkeypatch.setattr(runs, 'RUNS_DIR', tmp_path / 'runs')
        record = runner._run_one_conversation(execution_run_id='exec-test', suite_id='suite',
            scenario_id=case, iteration=1, payload=payload, agent_snapshot=target).model_dump(mode='json')
        assert calls == ['gpt-6-luna']
        assert record['tester_source'] == 'execution_failed' and record['tester_model_name'] is None
        assert build_assert_inference_row(run={'tester_model_name': 'another-conversation-model'}, conversation=record)['tester_model'] == 'execution_failed'
        return
    result = asyncio.run(runner._execute_waylo(execution_run_id='exec-test', conversation_id='conv-test',
        suite_id='suite', scenario_id=case, payload=payload,
        agent_snapshot=target, event_observer=None))
    assert len(calls) == invocations
    assert result['tester_source'] == source
    assert result['tester_model_name'] == ('gpt-6-luna' if invocations else None)
