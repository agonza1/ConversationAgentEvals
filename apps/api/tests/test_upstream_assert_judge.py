import json
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import app
from app.services import product_service, upstream_assert_judge
from app.services.assert_taxonomy_adapter import build_assert_taxonomy
from app.services.assert_transcript_adapter import build_assert_inference_row
from app.services.upstream_assert_judge import (
    UpstreamAssertJudgeBudgetExceeded,
    UpstreamAssertJudgeBusy,
    UpstreamAssertJudgeFailed,
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


def _scenario_contract():
    return {
        'title': 'Refund Policy Boundary',
        'goal': 'Open a policy review case without promising a refund.',
        'required_actions': ['open refund review case'],
        'forbidden_actions': ['promise guaranteed refund'],
        'expected_final_state': (
            'A refund review case exists and no completed refund is represented.'
        ),
    }


def _configure_assert_runtime(monkeypatch, tmp_path, *, daily_limit=200, max_n=3):
    monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')
    monkeypatch.setenv('ASSERT_JUDGE_MODEL', 'openai/gpt-4.1-mini')
    monkeypatch.setenv('ASSERT_JUDGE_ALLOWED_MODELS', 'openai/gpt-4.1-mini')
    monkeypatch.setenv('ASSERT_JUDGE_MAX_N', str(max_n))
    monkeypatch.setenv('ASSERT_JUDGE_MAX_CONCURRENT', '2')
    monkeypatch.setenv('LLM_JUDGE_API_KEY', 'test-key')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('LLM_JUDGE_DAILY_CREDIT_LIMIT', str(daily_limit))
    monkeypatch.setenv('LLM_JUDGE_RESERVED_DAILY_CREDITS', '0')
    monkeypatch.setattr(product_service, '_judge_spend_path', lambda: tmp_path / 'judge-spend.json')
    monkeypatch.setattr(upstream_assert_judge, '_ASSERT_JUDGE_ACTIVE', 0)
    product_service._reset_judge_spend_for_tests()


def _valid_score(run, conversation, judge_model, *, status='ok'):
    dimensions = {
        'policy_violation': False,
        'overrefusal': False,
        'required_behavior_failure': False,
        'unsupported_operational_claim': False,
        'resolution_failure': False,
    }
    taxonomy = build_assert_taxonomy(
        scenario_contract=_scenario_contract(),
        conversation=conversation,
    )
    node_judgments = [
        {
            'node_name': category['name'],
            'violated': False,
            'confidence': 'high',
            'reasoning': f"{category['name']} was not violated [1].",
        }
        for category in taxonomy['behavior_categories']
    ]
    return {
        'type': 'scenario',
        'test_case_id': conversation['conversation_id'],
        'target': run['agent_name'],
        'tester_model': run['tester_model_name'],
        'judge_model': judge_model,
        'judge_status': status,
        'judge_error': None,
        'verdict': {
            'dimensions': dimensions,
            'dimension_justifications': {
                name: f'{name} was not observed [1].'
                for name in dimensions
            },
            'node_judgments': node_judgments,
            'highlights': (
                '1. <cite id="4" description="Tool evidence">'
                '"case_id": "case-42"</cite>'
            ),
            'narrative': (
                'The caller asks for a refund after cancellation. '
                'The request remains focused on the disputed charge. '
                'The assistant opens a review case and explains that policy review remains. '
                'The conversation ends with a pending review case.'
            ),
        },
    }


def _install_fake_assert(monkeypatch, writer):
    monkeypatch.setattr(upstream_assert_judge.shutil, 'which', lambda name: '/venv/bin/assert-ai')
    captured = {}

    def fake_run(command, **kwargs):
        captured['command'] = command
        captured['env'] = kwargs['env']
        config_path = Path(command[command.index('--config') + 1])
        config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        score_path = Path(config['pipeline']['judge']['save_dir']) / 'scores.jsonl'
        writer(score_path, config)
        return subprocess.CompletedProcess(command, 0, stdout='{}', stderr='')

    monkeypatch.setattr(upstream_assert_judge.subprocess, 'run', fake_run)
    return captured


def _spent_credits():
    return int(product_service._load_judge_spend().get('spent') or 0)


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


def test_assert_inference_adapter_interleaves_explicit_action_anchors():
    run, conversation = _run_and_conversation()
    conversation['action_trace'] = [
        {
            'action': 'lookup_refund_policy',
            'before_turn_index': 2,
            'arguments': {'account_id': 'test-account'},
            'tool_result': {'eligible': True},
        },
        {
            'action': 'open_refund_review_case',
            'after_turn_index': 2,
            'arguments': {'account_id': 'test-account'},
            'tool_result': {'case_id': 'case-42'},
        },
    ]

    row = build_assert_inference_row(run=run, conversation=conversation)
    sequence = [
        (
            event['edit']['type'],
            event['edit'].get('tool_name')
            or event['edit'].get('message', {}).get('role'),
        )
        for event in row['events']
    ]

    assert sequence[:5] == [
        ('add_message', 'user'),
        ('tool_call', 'lookup_refund_policy'),
        ('add_message', 'assistant'),
        ('tool_call', 'open_refund_review_case'),
        ('tool_call', 'cae_final_state_snapshot'),
    ]


def test_assert_taxonomy_compiles_required_forbidden_and_evidence_failures():
    _, conversation = _run_and_conversation()
    taxonomy = build_assert_taxonomy(
        scenario_contract={
            'title': 'Refund Policy Boundary',
            'goal': 'Open a policy review case without promising a refund.',
            'required_actions': ['verify customer identity', 'open refund review case'],
            'forbidden_actions': ['promise guaranteed refund'],
            'expected_final_state': 'A refund review case remains pending policy review.',
        },
        conversation=conversation,
    )

    names = {item['name'] for item in taxonomy['behavior_categories']}
    assert 'missing_required_verify_customer_identity' in names
    assert 'missing_required_open_refund_review_case' in names
    assert 'forbidden_promise_guaranteed_refund' in names
    assert 'unsupported_operational_claim' in names
    assert all(item['permissible'] is False for item in taxonomy['behavior_categories'])
    assert taxonomy['behavior']['definition'] == (
        'Open a policy review case without promising a refund.\n\n'
        'Expected final state: A refund review case remains pending policy review.'
    )


def test_upstream_assert_judge_runs_existing_judge_only_command(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure_assert_runtime(monkeypatch, tmp_path)

    def writer(score_path, config):
        judge = config['pipeline']['judge']
        score_path.write_text(json.dumps(
            _valid_score(run, conversation, judge['model']['name'])
        ) + '\n', encoding='utf-8')

    captured = _install_fake_assert(monkeypatch, writer)

    response = run_upstream_assert_judge(
        run=run,
        conversation=conversation,
        scenario_contract=_scenario_contract(),
        artifact_root=tmp_path / 'assert-invocation',
    )

    assert captured['command'][:2] == ['/venv/bin/assert-ai', 'run']
    assert captured['command'][-2:] == ['--output', 'json']
    assert captured['env']['OPENAI_API_KEY'] == 'test-key'
    assert response['status'] == 'ready'
    assert response['engine'] == 'assert'
    assert response['provider'] == 'assert-ai'
    assert response['credits'] == 10
    assert response['spend_control']['spent_daily_credits'] == 10
    assert response['judge_result']['proposed_evaluation']['verdict'] == 'pass'
    provenance = response['judge_result']['provenance']
    assert provenance['engine'] == 'assert'
    assert provenance['judge_status'] == 'ok'
    assert provenance['assert_version'] == response['assert_version']
    assert provenance['input_fingerprint'] == response['input_fingerprint']
    assert provenance['artifacts'] == response['artifacts']
    assert provenance['dimensions']['policy_violation'] is False
    assert len(provenance['node_judgments']) == 4
    assert Path(response['artifacts']['scores']).exists()
    assert _spent_credits() == 10


@pytest.mark.parametrize('judge_status', ['judge_failed', 'filter_skipped', 'scoring_skipped'])
def test_upstream_assert_judge_rejects_non_ok_scores_and_refunds(
    monkeypatch,
    tmp_path,
    judge_status,
):
    run, conversation = _run_and_conversation()
    _configure_assert_runtime(monkeypatch, tmp_path)

    def writer(score_path, config):
        score = _valid_score(
            run,
            conversation,
            config['pipeline']['judge']['model']['name'],
            status=judge_status,
        )
        score['judge_error'] = f'simulated {judge_status}'
        if judge_status in {'filter_skipped', 'scoring_skipped'}:
            score['verdict'] = {}
        score_path.write_text(json.dumps(score) + '\n', encoding='utf-8')

    _install_fake_assert(monkeypatch, writer)

    with pytest.raises(UpstreamAssertJudgeFailed, match='valid judgment'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=_scenario_contract(),
            artifact_root=tmp_path / judge_status,
        )

    assert _spent_credits() == 0


def test_upstream_assert_judge_requires_exact_conversation_score(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure_assert_runtime(monkeypatch, tmp_path)

    def writer(score_path, config):
        score = _valid_score(run, conversation, config['pipeline']['judge']['model']['name'])
        score['test_case_id'] = 'another-conversation'
        score_path.write_text(json.dumps(score) + '\n', encoding='utf-8')

    _install_fake_assert(monkeypatch, writer)

    with pytest.raises(UpstreamAssertJudgeFailed, match='requested conversation'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=_scenario_contract(),
            artifact_root=tmp_path / 'missing-score',
        )

    assert _spent_credits() == 0


def test_upstream_assert_judge_rejects_malformed_custom_dimensions(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure_assert_runtime(monkeypatch, tmp_path)

    def writer(score_path, config):
        score = _valid_score(run, conversation, config['pipeline']['judge']['model']['name'])
        del score['verdict']['dimensions']['unsupported_operational_claim']
        score_path.write_text(json.dumps(score) + '\n', encoding='utf-8')

    _install_fake_assert(monkeypatch, writer)

    with pytest.raises(UpstreamAssertJudgeFailed, match='missing or non-boolean dimensions'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=_scenario_contract(),
            artifact_root=tmp_path / 'malformed-dimensions',
        )

    assert _spent_credits() == 0


def test_upstream_assert_judge_rejects_unexpected_taxonomy_nodes(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure_assert_runtime(monkeypatch, tmp_path)

    def writer(score_path, config):
        score = _valid_score(run, conversation, config['pipeline']['judge']['model']['name'])
        score['verdict']['node_judgments'][0]['node_name'] = 'invented_behavior'
        score_path.write_text(json.dumps(score) + '\n', encoding='utf-8')

    _install_fake_assert(monkeypatch, writer)

    with pytest.raises(UpstreamAssertJudgeFailed, match='unexpected node judgment'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=_scenario_contract(),
            artifact_root=tmp_path / 'malformed-node',
        )

    assert _spent_credits() == 0


def test_upstream_assert_judge_rejects_missing_taxonomy_nodes(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure_assert_runtime(monkeypatch, tmp_path)

    def writer(score_path, config):
        score = _valid_score(run, conversation, config['pipeline']['judge']['model']['name'])
        score['verdict']['node_judgments'].pop()
        score_path.write_text(json.dumps(score) + '\n', encoding='utf-8')

    _install_fake_assert(monkeypatch, writer)

    with pytest.raises(UpstreamAssertJudgeFailed, match='omitted taxonomy categories'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=_scenario_contract(),
            artifact_root=tmp_path / 'missing-node',
        )

    assert _spent_credits() == 0


def test_upstream_assert_judge_rejects_invalid_scores_jsonl(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure_assert_runtime(monkeypatch, tmp_path)
    _install_fake_assert(
        monkeypatch,
        lambda score_path, config: score_path.write_text('{not-json}\n', encoding='utf-8'),
    )

    with pytest.raises(UpstreamAssertJudgeFailed, match='invalid JSON'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=_scenario_contract(),
            artifact_root=tmp_path / 'invalid-json',
        )

    assert _spent_credits() == 0


def test_upstream_assert_judge_enforces_shared_daily_budget(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure_assert_runtime(monkeypatch, tmp_path, daily_limit=5)
    calls = 0

    def writer(score_path, config):
        nonlocal calls
        calls += 1

    _install_fake_assert(monkeypatch, writer)

    with pytest.raises(UpstreamAssertJudgeBudgetExceeded, match='daily credit budget'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=_scenario_contract(),
            artifact_root=tmp_path / 'budget',
        )

    assert calls == 0
    assert _spent_credits() == 0


def test_upstream_assert_judge_enforces_concurrency_limit(monkeypatch):
    monkeypatch.setenv('ASSERT_JUDGE_MAX_CONCURRENT', '1')
    monkeypatch.setattr(upstream_assert_judge, '_ASSERT_JUDGE_ACTIVE', 0)

    with upstream_assert_judge._assert_judge_slot():
        with pytest.raises(UpstreamAssertJudgeBusy, match='concurrency limit'):
            with upstream_assert_judge._assert_judge_slot():
                pass

    assert upstream_assert_judge._ASSERT_JUDGE_ACTIVE == 0


def test_upstream_assert_judge_restricts_models_and_multi_judge_cost(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure_assert_runtime(monkeypatch, tmp_path, max_n=1)

    with pytest.raises(ValueError, match='not allowed'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=_scenario_contract(),
            model_name='openai/gpt-4.1',
            artifact_root=tmp_path / 'model',
        )

    with pytest.raises(ValueError, match='between 1 and 1'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=_scenario_contract(),
            judge_n=2,
            artifact_root=tmp_path / 'judge-n',
        )


def test_upstream_assert_judge_requires_api_key_for_openai(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure_assert_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv('LLM_JUDGE_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr(upstream_assert_judge.shutil, 'which', lambda name: '/venv/bin/assert-ai')

    with pytest.raises(UpstreamAssertJudgeUnavailable, match='requires OPENAI_API_KEY'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=_scenario_contract(),
            artifact_root=tmp_path / 'provider',
        )

    assert _spent_credits() == 0


def test_upstream_assert_judge_is_explicitly_opt_in(monkeypatch):
    run, conversation = _run_and_conversation()
    monkeypatch.delenv('ASSERT_UPSTREAM_JUDGE_ENABLED', raising=False)

    with pytest.raises(UpstreamAssertJudgeUnavailable, match='ASSERT_UPSTREAM_JUDGE_ENABLED=1'):
        run_upstream_assert_judge(run=run, conversation=conversation)


@pytest.mark.parametrize(
    ('exception', 'status_code'),
    [
        (UpstreamAssertJudgeBusy('busy'), 429),
        (UpstreamAssertJudgeBudgetExceeded('budget'), 429),
        (UpstreamAssertJudgeUnavailable('provider'), 503),
        (UpstreamAssertJudgeFailed('failed'), 502),
    ],
)
def test_assert_judge_endpoint_maps_control_failures(monkeypatch, exception, status_code):
    from app.routes import assert_sidecar

    run, conversation = _run_and_conversation()
    monkeypatch.setattr(assert_sidecar.execution_run_store, 'get_execution_run', lambda run_id: run)
    monkeypatch.setattr(
        assert_sidecar.execution_run_store,
        'get_conversation',
        lambda run_id, conversation_id: conversation,
    )
    monkeypatch.setattr(
        assert_sidecar,
        'get_scenario_contract',
        lambda suite_id, scenario_id: {'goal': 'Review safely.'},
    )
    monkeypatch.setattr(
        assert_sidecar,
        'run_upstream_assert_judge',
        lambda **kwargs: (_ for _ in ()).throw(exception),
    )

    response = client.post(
        f"/api/assert/runs/{run['execution_run_id']}/conversations/{conversation['conversation_id']}/judge",
        json={'user_id': run['user_id']},
    )

    assert response.status_code == status_code
    assert response.json()['detail'] == str(exception)


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

    provenance = {
        'engine': 'assert',
        'assert_version': '0.1.0',
        'judge_status': 'ok',
        'input_fingerprint': 'fingerprint',
        'score_sha256': 'score-sha',
        'artifacts': {'scores': 'scores.jsonl'},
        'dimensions': {'policy_violation': False},
        'node_judgments': [],
    }
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
            'provenance': provenance,
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
        'artifacts': {'scores': 'scores.jsonl'},
        'assert_version': '0.1.0',
        'input_fingerprint': 'fingerprint',
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
    assert recorded['response']['judge_result']['provenance'] == provenance
