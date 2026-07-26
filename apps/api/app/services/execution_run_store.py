from __future__ import annotations

import hashlib
import json
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas.execution import (
    ConversationRecord,
    ExecutionRunProgress,
    ExecutionRunRecord,
    LiveExecutionEvent,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
RUNS_DIR = REPO_ROOT / 'artifacts' / 'execution-runs'

_LOCK = threading.Lock()
_RUNS: dict[str, dict[str, Any]] = {}


def reset_execution_runs_for_tests() -> None:
    with _LOCK:
        _RUNS.clear()


def create_execution_run(record: ExecutionRunRecord) -> dict[str, Any]:
    payload = record.model_dump(mode='json')
    with _LOCK:
        _RUNS[payload['execution_run_id']] = payload
        _persist_unlocked(payload)
    return deepcopy(payload)


def get_execution_run(execution_run_id: str) -> dict[str, Any] | None:
    with _LOCK:
        value = _RUNS.get(execution_run_id)
        if value is not None:
            return deepcopy(value)
    loaded = _load_from_disk(execution_run_id)
    if loaded is None:
        return None
    with _LOCK:
        _RUNS[execution_run_id] = loaded
        return deepcopy(loaded)


def get_conversation(execution_run_id: str, conversation_id: str) -> dict[str, Any] | None:
    run = get_execution_run(execution_run_id)
    if run is None:
        return None
    for item in run.get('conversations') or []:
        if item.get('conversation_id') == conversation_id:
            return deepcopy(item)
    return None


def list_execution_runs(
    *,
    user_id: str,
    project_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    _hydrate_from_disk()
    with _LOCK:
        rows = [deepcopy(item) for item in _RUNS.values() if item.get('user_id') == user_id]
    if project_id:
        rows = [item for item in rows if item.get('project_id') == project_id]
    if status:
        rows = [item for item in rows if item.get('status') == status]
    rows.sort(key=lambda item: item.get('updated_at') or '', reverse=True)
    return rows


def mark_execution_run_running(execution_run_id: str) -> dict[str, Any] | None:
    return _update_run(
        execution_run_id,
        status='running',
        progress_phase='executing',
    )


def mark_execution_run_failed(execution_run_id: str, error: str) -> dict[str, Any] | None:
    return _update_run(
        execution_run_id,
        status='failed',
        error=error,
        progress_phase='failed',
        completed=True,
    )


def upsert_conversation(execution_run_id: str, conversation: ConversationRecord) -> dict[str, Any] | None:
    with _LOCK:
        run = _RUNS.get(execution_run_id)
        if run is None:
            loaded = _load_from_disk(execution_run_id)
            if loaded is None:
                return None
            _RUNS[execution_run_id] = loaded
            run = loaded
        conversations = list(run.get('conversations') or [])
        payload = conversation.model_dump(mode='json')
        replaced = False
        for index, item in enumerate(conversations):
            if item.get('conversation_id') == payload['conversation_id']:
                conversations[index] = payload
                replaced = True
                break
        if not replaced:
            conversations.append(payload)
        completed = sum(1 for item in conversations if item.get('status') in {'completed', 'failed'})
        total = int((run.get('progress') or {}).get('total_conversations') or max(len(conversations), 1))
        percent = round((completed / total) * 100, 1) if total else 0.0
        active_id = None
        for item in reversed(conversations):
            if item.get('status') in {'queued', 'running'}:
                active_id = item.get('conversation_id')
                break
        run['conversations'] = conversations
        run['updated_at'] = _now()
        run['progress'] = ExecutionRunProgress(
            phase='executing',
            completed_conversations=completed,
            total_conversations=total,
            percent=percent,
            active_conversation_id=active_id,
        ).model_dump(mode='json')
        _persist_unlocked(run)
        return deepcopy(run)


def append_conversation(execution_run_id: str, conversation: ConversationRecord) -> dict[str, Any] | None:
    return upsert_conversation(execution_run_id, conversation)


def append_live_event(
    execution_run_id: str,
    conversation_id: str,
    event: LiveExecutionEvent,
) -> dict[str, Any] | None:
    """Append a current-run event without replacing the running conversation."""
    with _LOCK:
        run = _RUNS.get(execution_run_id)
        if run is None:
            loaded = _load_from_disk(execution_run_id)
            if loaded is None:
                return None
            _RUNS[execution_run_id] = loaded
            run = loaded
        conversations = list(run.get('conversations') or [])
        for conversation in conversations:
            if conversation.get('conversation_id') != conversation_id:
                continue
            events = list(conversation.get('live_events') or [])
            events.append(event.model_dump(mode='json'))
            conversation['live_events'] = events
            run['conversations'] = conversations
            run['updated_at'] = _now()
            _persist_unlocked(run)
            return deepcopy(run)
        return None


def update_live_event(
    execution_run_id: str,
    conversation_id: str,
    sequence: int,
    *,
    text: str,
    llm_output: str,
    asr_receipt: str,
    frame_metadata: dict[str, Any],
    kind: str | None = None,
    media_url: str | None = None,
    mime_type: str | None = None,
) -> dict[str, Any] | None:
    """Attach delayed media/ASR evidence without appending a duplicate turn."""
    with _LOCK:
        run = _RUNS.get(execution_run_id)
        if run is None:
            loaded = _load_from_disk(execution_run_id)
            if loaded is None:
                return None
            _RUNS[execution_run_id] = loaded
            run = loaded
        conversations = list(run.get('conversations') or [])
        for conversation in conversations:
            if conversation.get('conversation_id') != conversation_id:
                continue
            events = list(conversation.get('live_events') or [])
            for index, event in enumerate(events):
                if event.get('sequence') != sequence:
                    continue
                updated_fields = {
                    **event,
                    'text': text,
                    'llm_output': llm_output,
                    'asr_receipt': asr_receipt,
                    'frame_metadata': {
                        **(event.get('frame_metadata') or {}),
                        **frame_metadata,
                    },
                }
                if kind is not None:
                    updated_fields['kind'] = kind
                if media_url is not None:
                    updated_fields['media_url'] = media_url
                if mime_type is not None:
                    updated_fields['mime_type'] = mime_type
                events[index] = LiveExecutionEvent.model_validate(updated_fields).model_dump(mode='json')
                conversation['live_events'] = events
                run['conversations'] = conversations
                run['updated_at'] = _now()
                _persist_unlocked(run)
                return deepcopy(run)
            return None
        return None


def complete_execution_run(
    execution_run_id: str,
    *,
    status: str,
    inference_set_path: str | None = None,
    run_snapshot_path: str | None = None,
) -> dict[str, Any] | None:
    return _update_run(
        execution_run_id,
        status=status,
        inference_set_path=inference_set_path,
        run_snapshot_path=run_snapshot_path,
        progress_phase='completed' if status != 'failed' else 'failed',
        completed=True,
    )


def record_judge_review(
    execution_run_id: str,
    conversation_id: str,
    *,
    user_id: str,
    response: dict[str, Any],
    expected_deterministic_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a server-produced judge result as a pending, auditable review."""
    with _LOCK:
        run = _get_run_unlocked(execution_run_id)
        if run is None or run.get('user_id') != user_id:
            raise KeyError('Execution run not found.')
        if run.get('status') in {'queued', 'running'}:
            raise ValueError('LLM reviews can only be recorded for terminal execution runs.')
        conversation = _find_conversation_unlocked(run, conversation_id)
        if conversation is None:
            raise KeyError('Conversation not found.')
        if conversation.get('status') in {'queued', 'running'}:
            raise ValueError('LLM reviews can only be recorded for terminal conversations.')
        if response.get('status') != 'ready':
            raise ValueError('Only completed LLM reviews can be recorded.')
        current_snapshot = deterministic_evaluation_snapshot(conversation)
        if (
            expected_deterministic_snapshot is not None
            and expected_deterministic_snapshot != current_snapshot
        ):
            raise ValueError(
                'The deterministic evaluation changed while the LLM review was running. '
                'Run the review again.'
            )

        judge_result = deepcopy(response.get('judge_result') or {})
        raw_output = str(judge_result.pop('raw_output', '') or response.get('judge_output') or '')
        review_id = f'judge-review-{uuid.uuid4().hex[:16]}'
        created_at = _now()
        review = {
            'review_id': review_id,
            'status': 'pending_confirmation',
            'created_at': created_at,
            'provider': response.get('provider'),
            'model': response.get('model'),
            'latency_ms': response.get('latency_ms'),
            'message': response.get('message'),
            'evidence_citations': list(response.get('evidence_citations') or []),
            'judge_result': judge_result,
            'output_sha256': hashlib.sha256(raw_output.encode('utf-8')).hexdigest() if raw_output else None,
            'deterministic_snapshot': current_snapshot,
        }
        reviews = list(conversation.get('judge_reviews') or [])
        reviews.append(review)
        conversation['judge_reviews'] = _compact_judge_review_history(reviews)
        run['updated_at'] = created_at
        _persist_unlocked(run)
        return deepcopy(review)


def apply_judge_review(
    execution_run_id: str,
    conversation_id: str,
    *,
    user_id: str,
    review_id: str,
) -> dict[str, Any]:
    """Apply a confirmed judge proposal without replacing deterministic evidence."""
    with _LOCK:
        run = _get_run_unlocked(execution_run_id)
        if run is None or run.get('user_id') != user_id:
            raise KeyError('Execution run not found.')
        if run.get('status') in {'queued', 'running'}:
            raise ValueError('The execution run must be terminal before an adjudication can be applied.')
        conversation = _find_conversation_unlocked(run, conversation_id)
        if conversation is None:
            raise KeyError('Conversation not found.')
        if conversation.get('status') in {'queued', 'running'}:
            raise ValueError('The conversation must be terminal before an adjudication can be applied.')

        reviews = list(conversation.get('judge_reviews') or [])
        review = next((item for item in reviews if item.get('review_id') == review_id), None)
        if review is None:
            raise KeyError('LLM judge review not found.')
        if review.get('status') == 'applied':
            return deepcopy(run)
        if review.get('status') != 'pending_confirmation':
            raise ValueError('This LLM judge review is no longer available to apply.')
        proposed = (review.get('judge_result') or {}).get('proposed_evaluation')
        if not isinstance(proposed, dict):
            raise ValueError('This LLM judge review did not provide an evaluation update.')
        if review.get('deterministic_snapshot') != deterministic_evaluation_snapshot(conversation):
            raise ValueError('The deterministic evaluation changed after this LLM review. Run the review again.')

        applied_at = _now()
        previous = conversation.get('evaluation_adjudication')
        if isinstance(previous, dict):
            previous_id = previous.get('review_id')
            for item in reviews:
                if item.get('review_id') == previous_id and item.get('status') == 'applied':
                    item['status'] = 'superseded'
                    item['superseded_at'] = applied_at
                    break

        review['status'] = 'applied'
        review['applied_at'] = applied_at
        review['applied_by_user_id'] = user_id
        conversation['judge_reviews'] = reviews
        conversation['evaluation_adjudication'] = {
            'review_id': review_id,
            'source': 'llm_judge',
            'status': 'applied',
            'applied_at': applied_at,
            'applied_by_user_id': user_id,
            'provider': review.get('provider'),
            'model': review.get('model'),
            'latency_ms': review.get('latency_ms'),
            'output_sha256': review.get('output_sha256'),
            'evidence_citations': review.get('evidence_citations') or [],
            'judge_result': deepcopy(review.get('judge_result') or {}),
            'deterministic_snapshot': deepcopy(review.get('deterministic_snapshot') or {}),
        }
        run['status'] = _effective_run_status(run)
        run['updated_at'] = applied_at
        _persist_unlocked(run)
        return deepcopy(run)


def _update_run(
    execution_run_id: str,
    *,
    status: str | None = None,
    error: str | None = None,
    inference_set_path: str | None = None,
    run_snapshot_path: str | None = None,
    progress_phase: str | None = None,
    completed: bool = False,
) -> dict[str, Any] | None:
    with _LOCK:
        run = _RUNS.get(execution_run_id)
        if run is None:
            loaded = _load_from_disk(execution_run_id)
            if loaded is None:
                return None
            _RUNS[execution_run_id] = loaded
            run = loaded
        now = _now()
        if status is not None:
            run['status'] = status
        if error is not None:
            run['error'] = error
        if inference_set_path is not None:
            run['inference_set_path'] = inference_set_path
        if run_snapshot_path is not None:
            run['run_snapshot_path'] = run_snapshot_path
        progress = dict(run.get('progress') or {})
        if progress_phase is not None:
            progress['phase'] = progress_phase
        run['progress'] = progress
        run['updated_at'] = now
        if completed:
            run['completed_at'] = now
            progress['active_conversation_id'] = None
            run['progress'] = progress
        _persist_unlocked(run)
        return deepcopy(run)


def _get_run_unlocked(execution_run_id: str) -> dict[str, Any] | None:
    run = _RUNS.get(execution_run_id)
    if run is not None:
        return run
    loaded = _load_from_disk(execution_run_id)
    if loaded is not None:
        _RUNS[execution_run_id] = loaded
    return loaded


def _find_conversation_unlocked(run: dict[str, Any], conversation_id: str) -> dict[str, Any] | None:
    return next(
        (
            item for item in run.get('conversations') or []
            if item.get('conversation_id') == conversation_id
        ),
        None,
    )


def deterministic_evaluation_snapshot(conversation: dict[str, Any]) -> dict[str, Any]:
    summary = conversation.get('metrics_summary') or {}
    evidence = {
        'suite_id': conversation.get('suite_id'),
        'scenario_id': conversation.get('scenario_id'),
        'verdict': summary.get('verdict') or conversation.get('verdict'),
        'score': summary.get('score') if summary.get('score') is not None else conversation.get('score'),
        'evaluation_findings': conversation.get('evaluation_findings') or {},
        'action_trace': conversation.get('action_trace') or [],
        'final_state': conversation.get('final_state') or {},
        'transcript': conversation.get('transcript') or '',
        'turns': conversation.get('turns') or [],
        'error': conversation.get('error'),
    }
    serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)
    return {
        'verdict': evidence['verdict'],
        'score': evidence['score'],
        'evidence_sha256': hashlib.sha256(serialized.encode('utf-8')).hexdigest(),
    }


def _compact_judge_review_history(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bound unconfirmed proposals without discarding confirmed audit history."""
    pending_indexes = [
        index
        for index, review in enumerate(reviews)
        if review.get('status') == 'pending_confirmation'
    ]
    retained_pending_indexes = set(pending_indexes[-20:])
    return [
        review
        for index, review in enumerate(reviews)
        if review.get('status') != 'pending_confirmation' or index in retained_pending_indexes
    ]


def _effective_run_status(run: dict[str, Any]) -> str:
    conversations = list(run.get('conversations') or [])
    if any(item.get('status') == 'failed' for item in conversations):
        return 'failed'
    verdicts = []
    for conversation in conversations:
        adjudication = conversation.get('evaluation_adjudication') or {}
        proposal = (adjudication.get('judge_result') or {}).get('proposed_evaluation') or {}
        summary = conversation.get('metrics_summary') or {}
        verdicts.append(proposal.get('verdict') or summary.get('verdict') or conversation.get('verdict'))
    if verdicts and all(verdict == 'pass' for verdict in verdicts):
        return 'completed'
    return 'needs_review'


def _persist_unlocked(run: dict[str, Any]) -> None:
    execution_run_id = str(run.get('execution_run_id') or '')
    if not execution_run_id:
        return
    output_dir = RUNS_DIR / execution_run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / 'run.json'
    path.write_text(json.dumps(run, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    run['run_snapshot_path'] = str(path.relative_to(REPO_ROOT))


def _load_from_disk(execution_run_id: str) -> dict[str, Any] | None:
    path = RUNS_DIR / execution_run_id / 'run.json'
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _hydrate_from_disk() -> None:
    if not RUNS_DIR.exists():
        return
    with _LOCK:
        for path in RUNS_DIR.glob('*/run.json'):
            execution_run_id = path.parent.name
            if execution_run_id in _RUNS:
                continue
            loaded = _load_from_disk(execution_run_id)
            if loaded is not None:
                _RUNS[execution_run_id] = loaded


def _now() -> str:
    return datetime.now(UTC).isoformat()
