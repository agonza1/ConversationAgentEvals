from __future__ import annotations

import json
import re
from typing import Any

from assert_ai.core.transcript import (
    AddMessageEdit,
    Message,
    ToolCallEdit,
    Transcript,
    TranscriptEvent,
    TranscriptMetadata,
)

_USER_SPEAKERS = {'user', 'caller', 'customer', 'patient', 'learner', 'tester', 'human'}
_ASSISTANT_SPEAKERS = {'agent', 'assistant', 'target', 'bot', 'ai'}


def build_assert_inference_row(
    *,
    run: dict[str, Any],
    conversation: dict[str, Any],
) -> dict[str, Any]:
    """Convert CAE text, voice, action, and final-state evidence to ASSERT events."""
    scenario_id = str(conversation.get('scenario_id') or 'conversation')
    transcript = Transcript(
        metadata=TranscriptMetadata(
            kind='scenario',
            test_case_id=str(conversation.get('conversation_id') or 'conversation'),
            behavior=scenario_id,
            target=str(run.get('agent_name') or run.get('agent_id') or 'external-conversation-agent'),
            tester_model=str(run.get('tester_model_name') or run.get('tester_id') or 'cae-tester'),
            dimensions={
                'scenario': scenario_id,
                'mode': str(conversation.get('mode') or run.get('mode') or 'unknown'),
                'evidence_level': _evidence_level(conversation),
            },
        )
    )

    messages = _conversation_messages(conversation)
    actions = [
        (index, action, _action_anchor(action))
        for index, action in enumerate(conversation.get('action_trace') or [], start=1)
        if isinstance(action, dict)
    ]
    emitted_actions: set[int] = set()

    for turn_index, role, text, raw in messages:
        for action_index, action, anchor in actions:
            if action_index in emitted_actions or anchor != ('before', turn_index):
                continue
            transcript.add_event(_tool_event(action, action_index))
            emitted_actions.add(action_index)

        transcript.add_event(_message_event(role, text, raw=raw))

        for action_index, action, anchor in actions:
            if action_index in emitted_actions or anchor != ('after', turn_index):
                continue
            transcript.add_event(_tool_event(action, action_index))
            emitted_actions.add(action_index)

    for action_index, action, _anchor in actions:
        if action_index in emitted_actions:
            continue
        transcript.add_event(_tool_event(action, action_index))

    final_state = conversation.get('final_state')
    if isinstance(final_state, dict) and final_state:
        transcript.add_event(TranscriptEvent(
            view=['target', 'combined'],
            actor='tool',
            edit=ToolCallEdit(
                tool_name='cae_final_state_snapshot',
                tool_args={},
                tool_result=_json_text(final_state),
            ),
            raw={'cae_final_state': _jsonable(final_state)},
        ))

    error = conversation.get('error')
    if isinstance(error, str) and error.strip():
        transcript.add_event(_message_event(
            'system',
            f'[CAE execution error] {error.strip()}',
            raw={'cae_execution_error': error.strip()},
        ))

    if not transcript.events:
        raise ValueError('The conversation has no evidence to judge.')
    transcript.stop_reason = 'completed' if messages else 'evidence_only'
    return transcript.to_dict()


def _conversation_messages(
    conversation: dict[str, Any],
) -> list[tuple[int, str, str, dict[str, Any] | None]]:
    messages: list[tuple[int, str, str, dict[str, Any] | None]] = []
    for position, turn in enumerate(conversation.get('turns') or [], start=1):
        if not isinstance(turn, dict):
            continue
        role = _role(str(turn.get('speaker') or ''))
        text = str(turn.get('text') or '').strip()
        if not role or not text:
            continue
        turn_index = _positive_int(turn.get('turn_index')) or position
        messages.append((
            turn_index,
            role,
            text,
            {'cae_turn': _jsonable(turn)},
        ))
    if messages:
        return messages

    return [
        (index, role, text, None)
        for index, (role, text) in enumerate(
            _parse_transcript(str(conversation.get('transcript') or '')),
            start=1,
        )
    ]


def _message_event(role: str, text: str, raw: dict[str, Any] | None = None) -> TranscriptEvent:
    return TranscriptEvent(
        view=['target', 'combined'],
        actor='tester' if role == 'user' else 'target' if role == 'assistant' else 'system',
        edit=AddMessageEdit(message=Message(role=role, content=text)),
        raw=raw,
    )


def _tool_event(action: dict[str, Any], index: int) -> TranscriptEvent:
    return TranscriptEvent(
        view=['target', 'combined'],
        actor='tool',
        edit=ToolCallEdit(
            tool_name=_identifier(str(
                action.get('tool_name')
                or action.get('function')
                or action.get('action')
                or action.get('name')
                or action.get('type')
                or f'cae_action_{index}'
            )),
            tool_args=_action_args(action),
            tool_result=_action_result(action),
        ),
        raw={'cae_action': _jsonable(action)},
    )


def _action_anchor(action: dict[str, Any]) -> tuple[str, int] | None:
    for key in ('before_turn_index', 'before_turn', 'before_exchange'):
        value = _positive_int(action.get(key))
        if value is not None:
            return 'before', value
    for key in ('after_turn_index', 'after_turn', 'turn_index', 'exchange_index', 'exchange'):
        value = _positive_int(action.get(key))
        if value is not None:
            return 'after', value
    return None


def _parse_transcript(value: str) -> list[tuple[str, str]]:
    messages: list[tuple[str, str]] = []
    role: str | None = None
    lines: list[str] = []

    def flush() -> None:
        nonlocal role, lines
        text = '\n'.join(lines).strip()
        if role and text:
            messages.append((role, text))
        role, lines = None, []

    for line in value.splitlines():
        match = re.match(r'^\s*([A-Za-z][A-Za-z _-]{0,30})\s*:\s*(.*)$', line)
        next_role = _role(match.group(1)) if match else None
        if next_role:
            flush()
            role, lines = next_role, [match.group(2)]
        elif role:
            lines.append(line)
    flush()
    if not messages and value.strip():
        messages.append(('system', '[Unstructured CAE transcript evidence]\n' + value.strip()))
    return messages


def _role(value: str) -> str | None:
    speaker = value.strip().lower()
    if speaker in _USER_SPEAKERS or speaker.startswith(('user', 'caller', 'customer', 'patient', 'tester')):
        return 'user'
    if speaker in _ASSISTANT_SPEAKERS or speaker.startswith(('agent', 'assistant', 'target', 'bot')):
        return 'assistant'
    return None


def _action_args(action: dict[str, Any]) -> dict[str, Any]:
    for key in ('tool_args', 'arguments', 'args', 'parameters', 'input'):
        if isinstance(action.get(key), dict):
            return _jsonable(action[key])
    return _jsonable({
        key: value for key, value in action.items()
        if key not in {
            'tool_result',
            'result',
            'output',
            'response',
            'status',
            'error',
            'before_turn_index',
            'before_turn',
            'before_exchange',
            'after_turn_index',
            'after_turn',
            'turn_index',
            'exchange_index',
            'exchange',
        }
    })


def _action_result(action: dict[str, Any]) -> str:
    for key in ('tool_result', 'result', 'output', 'response'):
        if key in action:
            return _json_text(action[key])
    return _json_text(action)


def _evidence_level(conversation: dict[str, Any]) -> str:
    actions, state = bool(conversation.get('action_trace')), bool(conversation.get('final_state'))
    return 'gray_box' if actions and state else 'partial_structured' if actions or state else 'black_box'


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _json_text(value: Any, *, limit: int = 10000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= limit else text[:limit] + f'\n[... truncated, {len(text)} chars total ...]'


def _identifier(value: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '-', value).strip('-._')
    return (cleaned if cleaned and cleaned[0].isalnum() else f'cae-{cleaned or "value"}')[:120]
