from __future__ import annotations

import json
import threading
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
) -> dict[str, Any] | None:
    """Attach delayed ASR evidence to an audio event without appending a duplicate turn."""
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
                events[index] = LiveExecutionEvent.model_validate({
                    **event,
                    'text': text,
                    'llm_output': llm_output,
                    'asr_receipt': asr_receipt,
                    'frame_metadata': {
                        **(event.get('frame_metadata') or {}),
                        **frame_metadata,
                    },
                }).model_dump(mode='json')
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
