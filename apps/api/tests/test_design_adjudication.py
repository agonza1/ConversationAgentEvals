from assert_test_helpers import freeze, response_for
import pytest

from app.schemas.execution import ConversationRecord, ExecutionRunRecord, ExecutionRunProgress
from app.services import execution_run_store as store


def test_semantic_pass_cannot_override_blocked_design_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'RUNS_DIR', tmp_path)
    monkeypatch.setattr(store, 'REPO_ROOT', tmp_path)
    store.reset_execution_runs_for_tests()
    conversation = ConversationRecord(conversation_id='conversation', execution_run_id='exec-design',
        suite_id='suite', scenario_id='case', mode='text_callable', status='completed',
        transcript='Agent: Hello', verdict='needs_review',
        evaluation_findings={'design_enforcement': {'blocked': True, 'blocking_ids': ['final-state']}})
    record = ExecutionRunRecord(execution_run_id='exec-design', status='needs_review', mode='text_callable',
        suite_id='suite', scenario_ids=['case'], user_id='owner', project_id='project',
        progress=ExecutionRunProgress(phase='completed', completed_conversations=1, total_conversations=1, percent=100),
        conversations=[conversation], created_at='2026-10-07T00:00:00Z', updated_at='2026-10-07T00:00:00Z')
    payload = record.model_dump()
    freeze(payload['conversations'][0], {'goal': 'Check the recorded final state.'})
    conversation.evaluation_findings = payload['conversations'][0]['evaluation_findings']
    store.create_execution_run(ExecutionRunRecord.model_validate(payload))

    def review():
        run = store.get_execution_run('exec-design')
        conv = store.get_conversation('exec-design', 'conversation')
        return store.record_judge_review('exec-design', 'conversation', user_id='owner',
            response=response_for(run, conv, proposed='pass'))

    proposal = review()
    with pytest.raises(ValueError, match='design checks or evidence'):
        store.apply_judge_review('exec-design', 'conversation', user_id='owner', review_id=proposal['review_id'])
    assert store.get_conversation('exec-design', 'conversation').get('evaluation_adjudication') is None
    conversation.evaluation_findings['design_enforcement'] = {'blocked': False, 'blocking_ids': []}
    store.upsert_conversation('exec-design', conversation)
    fresh = review()
    result = store.apply_judge_review('exec-design', 'conversation', user_id='owner', review_id=fresh['review_id'])
    assert result['status'] == 'completed'
    assert result['conversations'][0]['evaluation_findings']['design_enforcement']['blocked'] is False
    store.reset_execution_runs_for_tests()
