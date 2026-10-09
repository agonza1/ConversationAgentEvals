"""Adapt persisted uploads/benchmarks to the same saved-conversation review path.

This is evidence replay, NOT an agent execution. Never accept a client report,
assert action completion from the transcript, or re-fetch a mutable contract.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import Lock

from sqlalchemy.orm import Session
from app.schemas.execution import ConversationRecord, ExecutionRunRecord, ExecutionRunProgress
from app.services import execution_run_store
from app.services.benchmark_run_store import get_benchmark_run
from app.services.evaluation_contract import content_hash, recorded_contract
from app.services.product_service import ensure_execution_product_project_id

_IMPORT_LOCK = Lock()


def import_benchmark_review(db: Session, *, user_id: str, benchmark_run_id: str) -> tuple[str, str]:
    record = get_benchmark_run(db=db, user_id=user_id, run_id=benchmark_run_id)
    if record is None:
        raise KeyError('Benchmark run not found.')
    report = record.get('report') or {}
    project = str(record.get('project_id') or '')
    binding = (report.get('run_metadata') or {}).get('product_project_id')
    transcript = record.get('transcript') or report.get('transcript') or ''
    # An old truncated preview is not sufficient evidence for a new semantic review.
    if not transcript.strip() and not report.get('action_trace') and not report.get('final_state'):
        raise ValueError('No complete retained evidence is available. Upload and evaluate the original evidence again.')
    from app.services.execution_runner import _compact_evaluation_findings
    findings = _compact_evaluation_findings(report)
    recorded_contract({'evaluation_findings': findings})  # validate historical provenance before mutations
    binding = ensure_execution_product_project_id(db=db, user_id=user_id, project_id=project,
                                                  product_project_id=binding, commit=False)
    identity = {'user_id': user_id, 'project_id': project, 'product_project_id': binding,
                'benchmark_run_id': benchmark_run_id, 'transcript': transcript,
                'action_trace': report.get('action_trace'), 'final_state': report.get('final_state'),
                'contract': report.get('evaluation_contract_snapshot'),
                'findings': {key: report.get(key) for key in ('verdict','overall_score','design_enforcement',
                    'programmatic_check_results','behavior_results')},
                'target': report.get('run_metadata')}
    # BenchmarkRunRequest accepts dictionaries, strings and lists for final_state.
    # Preserve opaque values as reported evidence, not as a fabricated state
    # snapshot that could be mistaken for proof of an executed business action.
    raw_final_state = report.get('final_state')
    structured_final_state = deepcopy(raw_final_state) if isinstance(raw_final_state, dict) else {}
    unstructured_final_state = (
        deepcopy(raw_final_state)
        if isinstance(raw_final_state, (str, list)) and bool(raw_final_state)
        else None
    )
    run_id = 'import-' + content_hash(identity)[:32]
    conversation_id = run_id + '-conversation'
    conversation = ConversationRecord(
        conversation_id=conversation_id, execution_run_id=run_id, suite_id=record['suite_id'],
        scenario_id=record['scenario_id'], scenario_title=report.get('scenario_title'),
        mode='text_callable', status='completed', transcript=transcript,
        action_trace=deepcopy(report.get('action_trace') or []), final_state=structured_final_state,
        **({'unstructured_final_state_evidence': unstructured_final_state}
           if unstructured_final_state is not None else {}),
        evaluation_findings=findings, verdict=report.get('verdict'),
        score=report.get('overall_score'), ietf_vcon_export=deepcopy(report.get('ietf_vcon_export')),
        vcon_export=deepcopy(report.get('vcon_export')), evidence_source='imported_benchmark',
        source_benchmark_run_id=benchmark_run_id,
    )
    with _IMPORT_LOCK:
        existing = execution_run_store.get_execution_run(run_id)
        if existing is not None:
            if existing.get('user_id') != user_id or existing.get('source_benchmark_run_id') != benchmark_run_id:
                raise ValueError('Imported evidence identity conflicts with an existing record.')
            db.commit()
            return run_id, conversation_id
        # The complete imported conversation has now passed schema/provenance
        # validation; only then make a newly resolved project durable.
        db.commit()
        now = datetime.now(UTC).isoformat()
        execution_run_store.create_execution_run(ExecutionRunRecord(
            execution_run_id=run_id, status='completed', mode='text_callable', suite_id=record['suite_id'],
            scenario_ids=[record['scenario_id']], user_id=user_id, project_id=project,
            product_project_id=binding, agent_name=(report.get('run_metadata') or {}).get('agent_version') or 'Imported conversation',
            tester_id='fixture_replay', executor_id='evidence_replay',
            source_benchmark_run_id=benchmark_run_id, evidence_source='imported_benchmark',
            live_external_connection=False, created_at=now, updated_at=now, completed_at=now,
            conversations=[conversation], progress=ExecutionRunProgress(
                phase='imported', completed_conversations=1, total_conversations=1, percent=100),
        ))
    return run_id, conversation_id
