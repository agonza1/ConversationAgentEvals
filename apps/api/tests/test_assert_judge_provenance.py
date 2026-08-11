import json

from app.schemas.execution import (
    ConversationRecord,
    ExecutionRunProgress,
    ExecutionRunRecord,
)
from app.services import execution_run_store


def test_assert_review_provenance_survives_pending_apply_and_disk_round_trip(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(execution_run_store, 'RUNS_DIR', tmp_path)
    execution_run_store.reset_execution_runs_for_tests()
    run_id = 'exec-assert-provenance'
    conversation_id = f'{run_id}-refund-1'
    execution_run_store.create_execution_run(ExecutionRunRecord(
        execution_run_id=run_id,
        status='completed',
        mode='text_callable',
        suite_id='call-center-voice-ai',
        scenario_ids=['refund-policy-boundary'],
        user_id='owner',
        project_id='project',
        progress=ExecutionRunProgress(
            phase='completed',
            completed_conversations=1,
            total_conversations=1,
            percent=100,
        ),
        created_at='2026-08-11T20:00:00+00:00',
        updated_at='2026-08-11T20:00:00+00:00',
        completed_at='2026-08-11T20:00:00+00:00',
    ))
    execution_run_store.upsert_conversation(run_id, ConversationRecord(
        conversation_id=conversation_id,
        execution_run_id=run_id,
        suite_id='call-center-voice-ai',
        scenario_id='refund-policy-boundary',
        mode='text_callable',
        status='completed',
        transcript='User: Refund this charge.\nAgent: I opened a refund review case.',
        final_state={'complete': False},
        verdict='needs_review',
        score=60,
    ))
    execution_run_store.complete_execution_run(run_id, status='needs_review')

    provenance = {
        'engine': 'assert',
        'assert_version': '0.1.0',
        'judge_status': 'ok',
        'input_fingerprint': 'input-fingerprint',
        'score_sha256': 'score-sha256',
        'artifacts': {
            'scores': 'artifacts/execution-runs/exec-assert-provenance/assert/scores.jsonl',
            'taxonomy': 'artifacts/execution-runs/exec-assert-provenance/assert/taxonomy.json',
        },
        'dimensions': {
            'policy_violation': False,
            'unsupported_operational_claim': False,
        },
        'node_judgments': [
            {
                'node_name': 'unsupported_operational_claim',
                'violated': False,
                'confidence': 'high',
                'reasoning': 'The action has matching evidence [1].',
            }
        ],
    }
    review = execution_run_store.record_judge_review(
        run_id,
        conversation_id,
        user_id='owner',
        response={
            'status': 'ready',
            'provider': 'assert-ai',
            'model': 'openai/gpt-4.1-mini',
            'judge_output': json.dumps({'judge_status': 'ok'}),
            'judge_result': {
                'agrees': True,
                'rationale': 'The evidence is consistent.',
                'next_action': 'Keep the deterministic result.',
                'provenance': provenance,
                'proposed_evaluation': {
                    'verdict': 'needs_review',
                    'summary': 'The review case exists, while the refund remains pending.',
                    'corrected_findings': [],
                    'remaining_gaps': ['Refund completion is not verified.'],
                },
            },
        },
    )

    pending = execution_run_store.get_conversation(run_id, conversation_id)
    assert pending is not None
    assert pending['judge_reviews'][0]['judge_result']['provenance'] == provenance

    applied = execution_run_store.apply_judge_review(
        run_id,
        conversation_id,
        user_id='owner',
        review_id=review['review_id'],
    )
    adjudication = applied['conversations'][0]['evaluation_adjudication']
    assert adjudication['judge_result']['provenance'] == provenance

    execution_run_store.reset_execution_runs_for_tests()
    reloaded = execution_run_store.get_conversation(run_id, conversation_id)
    assert reloaded is not None
    assert reloaded['evaluation_adjudication']['judge_result']['provenance'] == provenance
