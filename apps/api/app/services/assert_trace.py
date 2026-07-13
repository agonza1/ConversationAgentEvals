from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ACTION_NAME_KEYS = ('name', 'action', 'tool', 'tool_name', 'function', 'operation', 'type')
ACTION_ARGS_KEYS = ('arguments', 'args', 'input', 'inputs', 'parameters', 'params', 'payload')
ACTION_RESULT_KEYS = ('result', 'output', 'outputs', 'response')
ACTION_STATUS_KEYS = ('status', 'state', 'outcome')
FAILURE_VALUES = {'fail', 'failed', 'failure', 'error', 'errored', 'cancelled', 'canceled', False}


@dataclass(frozen=True)
class ActionEvent:
    name: str
    arguments: dict[str, Any]
    result: Any = None
    status: str | None = None
    raw: Any = None


def parse_action_trace(action_trace: Any) -> list[ActionEvent]:
    """Normalize supported ASSERT action/tool trace shapes into ordered events."""
    if action_trace is None:
        return []

    if isinstance(action_trace, dict):
        for key in ('actions', 'action_trace', 'trace', 'tool_calls', 'events', 'steps'):
            value = action_trace.get(key)
            if isinstance(value, list):
                return parse_action_trace(value)
        event = _event_from_mapping(action_trace)
        return [event] if event else []

    if not isinstance(action_trace, list):
        return []

    events: list[ActionEvent] = []
    for item in action_trace:
        if isinstance(item, dict):
            event = _event_from_mapping(item)
            if event:
                events.append(event)
        elif isinstance(item, str) and item.strip():
            events.append(ActionEvent(name=item.strip(), arguments={}, raw=item))
    return events


def _event_from_mapping(item: dict[str, Any]) -> ActionEvent | None:
    name = _first_present(item, ACTION_NAME_KEYS)
    if name is None and isinstance(item.get('tool_call'), dict):
        return _event_from_mapping(item['tool_call'])
    if name is None:
        return None

    arguments = _first_present(item, ACTION_ARGS_KEYS)
    if not isinstance(arguments, dict):
        arguments = {}

    result = _first_present(item, ACTION_RESULT_KEYS)
    status = _first_present(item, ACTION_STATUS_KEYS)
    return ActionEvent(name=str(name), arguments=arguments, result=result, status=str(status) if status is not None else None, raw=item)


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None
