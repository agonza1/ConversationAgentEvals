from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from threading import Lock
from typing import Any

_JUDGE_SPEND_LOCK = Lock()

def _judge_spend_path():
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    return root / 'artifacts' / 'llm_judge_spend.json'


def _reset_judge_spend_for_tests() -> None:
    with _JUDGE_SPEND_LOCK:
        path = _judge_spend_path()
        if path.exists():
            path.unlink(missing_ok=True)


def _load_judge_spend() -> dict[str, Any]:
    path = _judge_spend_path()
    today = datetime.now(UTC).date().isoformat()
    if not path.exists():
        return {'date': today, 'spent': 0}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {'date': today, 'spent': 0}
    if not isinstance(payload, dict) or payload.get('date') != today:
        return {'date': today, 'spent': 0}
    try:
        spent = int(payload.get('spent') or 0)
    except (TypeError, ValueError):
        spent = 0
    return {'date': today, 'spent': max(spent, 0)}


def _save_judge_spend(payload: dict[str, Any]) -> None:
    path = _judge_spend_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f'{path.name}.tmp')
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temp_path, path)


def _judge_spend_control() -> dict[str, Any]:
    daily_limit = max(0, _int_env('LLM_JUDGE_DAILY_CREDIT_LIMIT', 200))
    reserved_credits = max(0, _int_env('LLM_JUDGE_RESERVED_DAILY_CREDITS', 0))
    spent_credits = int(_load_judge_spend().get('spent') or 0)
    remaining = max(daily_limit - reserved_credits - spent_credits, 0)
    return {'estimated_credits': 10, 'daily_credit_limit': daily_limit,
            'reserved_daily_credits': reserved_credits, 'spent_daily_credits': spent_credits,
            'remaining_daily_credits': remaining, 'provider': 'assert-ai',
            'within_budget': remaining >= 10, 'budget_date': datetime.now(UTC).date().isoformat()}


def _with_spend_totals(spend_control: dict[str, Any], spent_credits: int, *, credits: int) -> dict[str, Any]:
    updated = dict(spend_control)
    updated['spent_daily_credits'] = spent_credits
    daily_limit = int(updated.get('daily_credit_limit', 200))
    reserved = int(updated.get('reserved_daily_credits') or 0)
    updated['remaining_daily_credits'] = max(daily_limit - reserved - spent_credits, 0)
    updated['within_budget'] = updated['remaining_daily_credits'] >= credits
    return updated


def _reserve_judge_credits(spend_control: dict[str, Any], *, credits: int) -> tuple[bool, dict[str, Any]]:
    with _JUDGE_SPEND_LOCK:
        state = _load_judge_spend()
        spent = int(state.get('spent') or 0)
        daily_limit = int(spend_control.get('daily_credit_limit', 200))
        reserved = int(spend_control.get('reserved_daily_credits') or 0)
        remaining = max(daily_limit - reserved - spent, 0)
        if remaining < credits:
            return False, _with_spend_totals(spend_control, spent, credits=credits)
        next_spent = spent + credits
        state['spent'] = next_spent
        _save_judge_spend(state)
        return True, _with_spend_totals(spend_control, next_spent, credits=credits)


def _refund_judge_credits(spend_control: dict[str, Any], *, credits: int) -> dict[str, Any]:
    with _JUDGE_SPEND_LOCK:
        state = _load_judge_spend()
        if spend_control.get('budget_date') and spend_control['budget_date'] != state.get('date'):
            return _with_spend_totals(spend_control, int(state.get('spent') or 0), credits=credits)
        next_spent = max(int(state.get('spent') or 0) - credits, 0)
        state['spent'] = next_spent
        _save_judge_spend(state)
        return _with_spend_totals(spend_control, next_spent, credits=credits)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


