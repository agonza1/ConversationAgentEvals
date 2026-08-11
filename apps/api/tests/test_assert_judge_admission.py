import pytest

from app.services import product_service, upstream_assert_judge
from app.services.upstream_assert_judge import (
    UpstreamAssertJudgeBudgetExceeded,
    UpstreamAssertJudgeBusy,
    run_upstream_assert_judge,
)


def _run_and_conversation():
    run = {
        'execution_run_id': 'exec-admission',
        'status': 'completed',
        'mode': 'text_callable',
        'suite_id': 'call-center-voice-ai',
        'user_id': 'owner',
        'agent_name': 'External agent',
        'tester_id': 'scenario_simulator',
    }
    conversation = {
        'conversation_id': 'exec-admission-refund-1',
        'execution_run_id': 'exec-admission',
        'suite_id': 'call-center-voice-ai',
        'scenario_id': 'refund-policy-boundary',
        'scenario_title': 'Refund Policy Boundary',
        'mode': 'text_callable',
        'status': 'completed',
        'turns': [
            {'turn_index': 1, 'speaker': 'user', 'text': 'Please review this refund.'},
            {'turn_index': 2, 'speaker': 'agent', 'text': 'I can open a review case.'},
        ],
        'verdict': 'needs_review',
    }
    return run, conversation


def _configure(monkeypatch, tmp_path, *, daily_limit=200, max_concurrent=1):
    monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')
    monkeypatch.setenv('ASSERT_JUDGE_MODEL', 'openai/gpt-4.1-mini')
    monkeypatch.setenv('ASSERT_JUDGE_ALLOWED_MODELS', 'openai/gpt-4.1-mini')
    monkeypatch.setenv('ASSERT_JUDGE_MAX_N', '1')
    monkeypatch.setenv('ASSERT_JUDGE_MAX_CONCURRENT', str(max_concurrent))
    monkeypatch.setenv('LLM_JUDGE_API_KEY', 'test-key')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('LLM_JUDGE_DAILY_CREDIT_LIMIT', str(daily_limit))
    monkeypatch.setenv('LLM_JUDGE_RESERVED_DAILY_CREDITS', '0')
    monkeypatch.setattr(product_service, '_judge_spend_path', lambda: tmp_path / 'judge-spend.json')
    monkeypatch.setattr(upstream_assert_judge.shutil, 'which', lambda name: '/venv/bin/assert-ai')
    monkeypatch.setattr(
        upstream_assert_judge.subprocess,
        'run',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError('ASSERT subprocess must not start before admission succeeds')
        ),
    )
    monkeypatch.setattr(upstream_assert_judge, '_ASSERT_JUDGE_ACTIVE', 0)
    product_service._reset_judge_spend_for_tests()


def test_budget_rejection_creates_no_invocation_artifacts(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure(monkeypatch, tmp_path, daily_limit=5)
    artifact_root = tmp_path / 'budget-rejected-invocation'

    with pytest.raises(UpstreamAssertJudgeBudgetExceeded, match='daily credit budget'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract={
                'required_actions': ['open refund review case'],
                'forbidden_actions': ['promise guaranteed refund'],
            },
            artifact_root=artifact_root,
        )

    assert not artifact_root.exists()
    assert product_service._load_judge_spend()['spent'] == 0


def test_concurrency_rejection_creates_no_invocation_artifacts(monkeypatch, tmp_path):
    run, conversation = _run_and_conversation()
    _configure(monkeypatch, tmp_path, max_concurrent=1)
    monkeypatch.setattr(upstream_assert_judge, '_ASSERT_JUDGE_ACTIVE', 1)
    artifact_root = tmp_path / 'busy-rejected-invocation'

    with pytest.raises(UpstreamAssertJudgeBusy, match='concurrency limit'):
        run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract={
                'required_actions': ['open refund review case'],
                'forbidden_actions': ['promise guaranteed refund'],
            },
            artifact_root=artifact_root,
        )

    assert not artifact_root.exists()
    assert product_service._load_judge_spend()['spent'] == 0
