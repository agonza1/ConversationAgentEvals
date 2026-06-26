from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Literal


ASSERT_QUEUE_LIFECYCLE_VERSION = 'assert-queue-lifecycle-v1'
AssertQueueStatus = Literal['queued', 'running', 'completed', 'failed', 'canceled']
TERMINAL_STATUSES = {'completed', 'failed', 'canceled'}


def create_queue_state(
    *,
    run_id: str,
    max_attempts: int = 1,
    cost_limit_usd: float | None = None,
    estimated_cost_usd: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = _timestamp(now)
    _validate_attempts(max_attempts)
    _validate_cost_limit(cost_limit_usd=cost_limit_usd, estimated_cost_usd=estimated_cost_usd)
    return {
        'version': ASSERT_QUEUE_LIFECYCLE_VERSION,
        'run_id': run_id,
        'status': 'queued',
        'terminal': False,
        'attempt': 1,
        'max_attempts': max_attempts,
        'retryable': False,
        'cancel_requested': False,
        'cost_limit_usd': cost_limit_usd,
        'estimated_cost_usd': estimated_cost_usd,
        'spent_cost_usd': 0.0,
        'queued_at': timestamp,
        'started_at': None,
        'completed_at': None,
        'failed_at': None,
        'canceled_at': None,
        'transitions': [{'from': None, 'to': 'queued', 'at': timestamp, 'reason': 'ASSERT run accepted'}],
    }


def mark_running(state: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    current = _copy_active_state(state)
    _require_status(current, {'queued'})
    return _transition(current, 'running', now=now, reason='ASSERT worker started', timestamp_field='started_at')


def mark_completed(state: dict[str, Any], *, spent_cost_usd: float | None = None, now: datetime | None = None) -> dict[str, Any]:
    current = _copy_active_state(state)
    _require_status(current, {'running'})
    if spent_cost_usd is not None:
        current['spent_cost_usd'] = _non_negative_cost(spent_cost_usd)
    return _transition(current, 'completed', now=now, reason='ASSERT result manifest stored', timestamp_field='completed_at')


def mark_failed(state: dict[str, Any], *, reason: str, spent_cost_usd: float | None = None, now: datetime | None = None) -> dict[str, Any]:
    current = _copy_active_state(state)
    _require_status(current, {'queued', 'running'})
    if spent_cost_usd is not None:
        current['spent_cost_usd'] = _non_negative_cost(spent_cost_usd)
    failed = _transition(current, 'failed', now=now, reason=reason, timestamp_field='failed_at')
    failed['error'] = reason
    failed['retryable'] = int(failed.get('attempt') or 1) < int(failed.get('max_attempts') or 1)
    return failed


def request_cancel(state: dict[str, Any], *, reason: str = 'cancel requested', now: datetime | None = None) -> dict[str, Any]:
    current = _copy_active_state(state)
    _require_status(current, {'queued', 'running'})
    current['cancel_requested'] = True
    return _transition(current, 'canceled', now=now, reason=reason, timestamp_field='canceled_at')


def retry_from_failure(state: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    current = deepcopy(state)
    _require_status(current, {'failed'})
    attempt = int(current.get('attempt') or 1)
    max_attempts = int(current.get('max_attempts') or 1)
    if attempt >= max_attempts:
        raise ValueError('ASSERT run has exhausted retry attempts')
    retried = create_queue_state(
        run_id=str(current.get('run_id') or ''),
        max_attempts=max_attempts,
        cost_limit_usd=current.get('cost_limit_usd'),
        estimated_cost_usd=current.get('estimated_cost_usd'),
        now=now,
    )
    retried['attempt'] = attempt + 1
    retried['retry_parent_attempt'] = attempt
    retried['transitions'][0]['reason'] = 'retry queued after ASSERT worker failure'
    return retried


def enforce_cost_limit(state: dict[str, Any], *, estimated_cost_usd: float, now: datetime | None = None) -> dict[str, Any]:
    current = _copy_active_state(state)
    estimate = _non_negative_cost(estimated_cost_usd)
    current['estimated_cost_usd'] = estimate
    limit = current.get('cost_limit_usd')
    if limit is not None and estimate > float(limit):
        failed = _transition(
            current,
            'failed',
            now=now,
            reason='estimated ASSERT cost exceeds run cost limit',
            timestamp_field='failed_at',
        )
        failed['error'] = 'estimated ASSERT cost exceeds run cost limit'
        failed['retryable'] = False
        return failed
    return current


def _transition(
    state: dict[str, Any],
    status: AssertQueueStatus,
    *,
    now: datetime | None,
    reason: str,
    timestamp_field: str,
) -> dict[str, Any]:
    timestamp = _timestamp(now)
    previous = state.get('status')
    state['status'] = status
    state['terminal'] = status in TERMINAL_STATUSES
    state['retryable'] = False if status in {'completed', 'canceled'} else bool(state.get('retryable'))
    state[timestamp_field] = timestamp
    state['updated_at'] = timestamp
    state.setdefault('transitions', []).append({'from': previous, 'to': status, 'at': timestamp, 'reason': reason})
    return state


def _copy_active_state(state: dict[str, Any]) -> dict[str, Any]:
    current = deepcopy(state)
    if current.get('status') in TERMINAL_STATUSES:
        raise ValueError(f"ASSERT run is already terminal: {current.get('status')}")
    return current


def _require_status(state: dict[str, Any], allowed: set[str]) -> None:
    status = state.get('status')
    if status not in allowed:
        raise ValueError(f'ASSERT run cannot transition from {status}')


def _validate_attempts(max_attempts: int) -> None:
    if isinstance(max_attempts, bool) or int(max_attempts) < 1:
        raise ValueError('max_attempts must be a positive integer')


def _validate_cost_limit(*, cost_limit_usd: float | None, estimated_cost_usd: float | None) -> None:
    if cost_limit_usd is not None:
        _non_negative_cost(cost_limit_usd)
    if estimated_cost_usd is not None:
        estimate = _non_negative_cost(estimated_cost_usd)
        if cost_limit_usd is not None and estimate > float(cost_limit_usd):
            raise ValueError('estimated ASSERT cost exceeds run cost limit')


def _non_negative_cost(value: float) -> float:
    parsed = float(value)
    if parsed < 0:
        raise ValueError('cost values must be non-negative')
    return parsed


def _timestamp(now: datetime | None) -> str:
    current = now or datetime.now(UTC)
    return current.isoformat().replace('+00:00', 'Z')
