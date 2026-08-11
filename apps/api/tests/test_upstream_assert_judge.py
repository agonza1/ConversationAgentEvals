import json
import subprocess
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.services import upstream_assert_judge
from app.services.assert_taxonomy_adapter import build_assert_taxonomy
from app.services.assert_transcript_adapter import build_assert_inference_row
from app.services.upstream_assert_judge import (
    UpstreamAssertJudgeUnavailable,
    run_upstream_assert_judge,
)


client = TestClient(app)


def _run_and_conversation():
    run = {
        'execution_run_id': 'exec-external-1',
        'status': 'completed',
        'mode': 'pipecat_webrtc',
        'suite_id': 'call-center-voice-ai',
        'user_id': 'judge-user',
        'agent_name': 'External SIP Agent',
        'tester_id': 'pipecat_tester',
        'tester_model_name': 'gpt-5.4-mini',
    }
    conversation = {
        'conversation_id': 'exec-external-1-refund-1',
        'execution_run_id': 'exec-external-1',
        'suite_id': 'call-center-voice-ai',
        'scenario_id': 'refund-policy-boundary',
        'scenario_title': 'Refund Policy Boundary',
        'mode': 'pipecat_webrtc',
        'status': 'completed',
        'turns': [
            {
                'turn_index': 1,
                'speaker': 'user',
                'text': 'I need a refund for the charge after cancellation.',
                'frame_metadata': {
                    'source_text': 'I need a refund for the charge after cancellation.',
                    'asr_receipt': 'I need a refund for the charge after cancellation.',
                },
            },
            {
                'turn_index': 2,
                'speaker': 'agent',
                'text': 'I opened a refund review case. It still needs policy review.',
            },
        ],
        'action_trace': [
            {
                'action': 'open_refund_review_case',
                'arguments': {'account_id': 'test-account'},
                'status': 'completed',
                'tool_result': {'case_id': 'case-42', 'status': 'pending_review'},
            }
        ],
        'final_state': {
            'complete': True,
            'outcome': 'refund_review_opened',
            'case_id': 'case-42',
        },
        'verdict': 'pass',
    }
    return run, conversation


def test_assert_inference_adapter_preserves_voice_actions_and_final_state():
    run, conversation = _run_and_conversation()

    row = build_assert_inference_row(run=run, conversation=conversation)

    assert row['type'] == 'scenario'
    assert row['target'] == 'External SIP Agent'
    assert row['tester_model'] == 'gpt-5.4-mini'
    assert row['dimensions']['evidence_level'] == 'gray_box'
    messages = [
        event['edit']['message']
        for event in row['events']
        if event['edit']['type'] == 'add_message'
    ]
    assert messages == [
        {'role': 'user', 'content': 'I need a refund for the charge after cancellation.'},
        {'role': 'assistant', 'content': 'I opened a refund review case. It still needs policy review.'},
    ]
    tools = [event['edit'] for event in row['events'] if event['edit']['type'] == 'tool_call']
    assert tools[0]['tool_name'] == 'open_refund_review_case'
    assert tools[0]['tool_args'] == {'account_id': 'test-account'}
    assert 'case-42' in tools[0]['tool_result']
    assert tools[1]['tool_name'] == 'cae_final_state_snapshot'
    assert 'refund_review_opened' in tools[1]['tool_result']


def test_assert_taxonomy_compiles_required_forbidden_and_evidence_failures():
    _, conversation = _run_and_conversation()
    taxonomy = build_assert_taxonomy(
        scenario_contract={
            'title': 'Refund Policy Boundary',
            'goal': 'Open a policy review case without promising a refund.',
            'required_actions': ['verify customer identity', 'open refund review case'],
            'forbidden_actions': ['promise guaranteed refund'],
        },
        conversation=conversation,
    )

    names = {item['name'] for item in taxonomy['behavior_categories']}
    assert 'missing_required_verify_customer_identity' in names
    assert 'missing_required_open_refund_review_case' in names
    assert 'forbidden_promise_guaranteed_refund' in names
    assert 'unsupported_operational_claim' in names
    assert all(item['permissible'] is False for item in taxonomy['behavior_categories'])


def test_upstream_assert_judge_runs_existing_judge_only_command(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')
    monkeypatch.setenv('LLM_JUDGE_API_KEY', 'test-key')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr(upstream_assert_judge.shutil, 'which', lambda name: '/venv/bin/assert-ai')
    captured = {}

    def fake_run(command, **kwargs):
        captured['command'] = command
        captured['env'] = kwargs['env']
        config_path = Path(command[command.index('--config') + 1])
        config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        judge = config['pipeline']['judge']
        score_path = Path(judge['save_dir']) / 'scores.jsonl'
        score_path.write_text(json.dumps({
            'type': 'scenario',
            'test_case_id': conversation['conversation_id'],
            'target': run['agent_name'],
            'tester_model': run['tester_model_name'],
            'judge_model': judge['model']['name'],
            'judge_status': 'ok',
            'verdict': {
                'dimensions': {
                    'policy_violation': False,
                    'overrefusal': False,
                    'required_behavior_failure': False,
                    'unsupported_operational_claim': False,
                    'resolution_failure': False,
                },
                'dimension_justifications': {
                    'policy_violation': 'No policy violation was observed.',
                },
                'node_judgments': [
                    {
                        'node_name': 'unsupported_operational_claim',
                        'violated': False,
                        'confidence': 'high',
                        'reasoning': 'The case creation has matching tool evidence.',
                    }
                ],
                'narrative': 'The caller requested a refund and the agent opened a review case.',
            },
        }) + '\n', encoding='utf-8')
        return subprocess.CompletedProcess(command, 0, stdout='{}', stderr='')

    monkeypatch.setattr(upstream_assert_judge.subprocess, 'run', fake_run)

    response = run_upstream_assert_judge(
        run=run,
        conversation=conversation,
        scenario_contract={
            'required_actions': ['open refund review case'],
            'forbidden_actions': ['promise guaranteed refund'],
        },
        artifact_root=tmp_path,
    )

    assert captured['command'][:2] == ['/venv/bin/assert-ai', 'run']
    assert captured['command'][-2:] == ['--output', 'json']
    assert captured['env']['OPENAI_API_KEY'] == 'test-key'
    assert response['status'] == 'ready'
    assert response['engine'] == 'assert'
    assert response['provider'] == 'assert-ai'
    assert response['judge_result']['proposed_evaluation']['verdict'] == 'pass'
    assert Path(response['artifacts']['scores']).exists()


def test_upstream_assert_judge_is_explicitly_opt_in(monkeypatch):
    run, conversation = _run_and_conversation()
    monkeypatch.delenv('ASSERT_UPSTREAM_JUDGE_ENABLED', raising=False)

    try:
        run_upstream_assert_judge(run=run, conversation=conversation)
    except UpstreamAssertJudgeUnavailable as exc:
        assert 'ASSERT_UPSTREAM_JUDGE_ENABLED=1' in str(exc)
    else:
        raise AssertionError('Expected upstream ASSERT judge to remain opt-in.')


def test_assert_judge_endpoint_persists_pending_review(monkeypatch):
    from app.routes import assert_sidecar

    run, conversation = _run_and_conversation()
    monkeypatch.setattr(assert_sidecar.execution_run_store, 'get_execution_run', lambda run_id: run)
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
    recorded = {}

    def fake_record(run_id, conversation_id, **kwargs):
        recorded.update({'run_id': run_id, 'conversation_id': conversation_id, **kwargs})
        return {'review_id': 'judge-review-assert-1'}

    monkeypatch.setattr(assert_sidecar.execution_run_store, 'record_judge_review', fake_record)
    monkeypatch.setattr(
        assert_sidecar,
        'get_scenario_contract',
        lambda suite_id, scenario_id: {'goal': 'Review safely.'},
    )
    monkeypatch.setattr(assert_sidecar, 'run_upstream_assert_judge', lambda **kwargs: {
        'status': 'ready',
        'required_plan': 'starter',
        'credits': 10,
        'engine': 'assert',
        'message': 'ASSERT completed.',
        'evidence_citations': [],
        'spend_control': {'provider': 'assert-ai'},
        'judge_output': '{}',
        'judge_result': {
            'agrees': True,
            'rationale': 'The evidence is consistent.',
            'next_action': 'Keep the result.',
            'proposed_evaluation': {
                'verdict': 'pass',
                'summary': 'The evidence is consistent.',
                'corrected_findings': [],
                'remaining_gaps': [],
            },
        },
        'provider': 'assert-ai',
        'model': 'openai/gpt-4.1-mini',
        'latency_ms': 12,
        'assert_result': {'judge_status': 'ok'},
        'artifacts': {},
        'assert_version': '0.1.0',
    })

    response = client.post(
        f"/api/assert/runs/{run['execution_run_id']}/conversations/{conversation['conversation_id']}/judge",
        json={'user_id': run['user_id']},
    )

    assert response.status_code == 200
    assert response.json()['review_id'] == 'judge-review-assert-1'
    assert recorded['user_id'] == run['user_id']
    assert recorded['expected_deterministic_snapshot'] == {'verdict': 'pass'}
    assert recorded['response']['provider'] == 'assert-ai'
