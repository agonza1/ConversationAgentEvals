"""Finite structured event checks shared by catalog and authored evidence reports."""
from __future__ import annotations
from typing import Any
from app.services.assert_trace import parse_action_trace


def _evaluate_check(
    check: dict[str, Any],
    *,
    event_names: list[str],
    final_state: dict[str, Any],
) -> tuple[bool, Any]:
    kind = check.get('kind')
    if kind == 'event_any':
        expected = {_normalize_event_name(value) for value in check.get('event_types', [])}
        observed = [name for name in event_names if name in expected]
        return bool(observed), observed

    if kind == 'event_order':
        before = _first_event_position(event_names, check.get('before_any', []))
        after = _first_event_position(event_names, check.get('after_any', []))
        return before is not None and after is not None and before < after, {'before': before, 'after': after}

    if kind == 'final_state_equals':
        observed = _value_at_path(final_state, str(check.get('path') or ''))
        return observed == check.get('value'), observed

    if kind == 'final_state_in':
        observed = _value_at_path(final_state, str(check.get('path') or ''))
        return observed in set(check.get('values') or []), observed

    if kind == 'conditional_event':
        trigger = _first_event_position(event_names, check.get('if_any', []))
        if trigger is None:
            return True, {'triggered': False}
        resolution = _first_event_position(event_names, check.get('then_any', []), start=trigger + 1)
        return resolution is not None, {'triggered': True, 'trigger': trigger, 'resolution': resolution}

    return False, {'unsupported_kind': kind}

def _first_event_position(event_names: list[str], expected: list[str], *, start: int = 0) -> int | None:
    normalized = {_normalize_event_name(value) for value in expected}
    for index, event_name in enumerate(event_names[start:], start=start):
        if event_name in normalized:
            return index
    return None

def _event_type_names_from_action_trace(action_trace: Any) -> list[str]:
    """Prefer ACC event `type` over human-readable `action` labels for deterministic checks."""

    if isinstance(action_trace, dict):
        for key in ('actions', 'action_trace', 'trace', 'tool_calls', 'events', 'steps'):
            value = action_trace.get(key)
            if isinstance(value, list):
                return _event_type_names_from_action_trace(value)
        return _event_type_names_from_action_trace([action_trace])

    if not isinstance(action_trace, list):
        return [_normalize_event_name(event.name) for event in parse_action_trace(action_trace)]

    names: list[str] = []
    for item in action_trace:
        if isinstance(item, dict):
            event_type = item.get('type')
            if isinstance(event_type, str) and event_type.strip():
                names.append(_normalize_event_name(event_type))
                continue
        for event in parse_action_trace([item] if not isinstance(item, list) else item):
            names.append(_normalize_event_name(event.name))
    return names

def _normalize_event_name(value: Any) -> str:
    return str(value).strip().lower().replace('-', '_').replace(' ', '_')

def _value_at_path(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split('.'):
        if not part:
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def structured_outcome(check: dict, passed: bool, observed: Any) -> str:
    """Absent observations do not demonstrate a failed business action."""
    kind = check.get('kind')
    if check.get('applicable') is False:
        return 'not_applicable'
    if passed:
        if kind == 'conditional_event' and isinstance(observed, dict) and observed.get('triggered') is False:
            return 'not_applicable'
        return 'pass'
    if kind == 'event_order' and isinstance(observed, dict) and all(observed.get(k) is not None for k in ('before', 'after')):
        return 'fail'
    if kind in {'final_state_equals', 'final_state_in'} and observed is not None:
        return 'fail'
    return 'not_observable'
