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

    message_count = 0
    for turn in conversation.get('turns') or []:
        if not isinstance(turn, dict):
            continue
        role = _role(str(turn.get('speaker') or ''))
        text = str(turn.get('text') or '').strip()
        if role and text:
            transcript.add_event(_message_event(role, text, raw={'cae_turn': _jsonable(turn)}))
            message_count += 1
    if message_count == 0:
        for role, text in _parse_transcript(str(conversation.get('transcript') or '')):
            transcript.add_event(_message_event(role, text))
            message_count += int(role in {'user', 'assistant'})

    for index, action in enumerate(conversation.get('action_trace') or [], start=1):
        if not isinstance(action, dict):
            continue
        transcript.add_event(TranscriptEvent(
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
        ))

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
    transcript.stop_reason = 'completed' if message_count else 'evidence_only'
    return transcript.to_dict()


def _message_event(role: str, text: str, raw: dict[str, Any] | None = None) -> TranscriptEvent:
    return TranscriptEvent(
        view=['target', 'combined'],
        actor='tester' if role == 'user' else 'target' if role == 'assistant' else 'system',
        edit=AddMessageEdit(message=Message(role=role, content=text)),
        raw=raw,
    )


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
        if key not in {'tool_result', 'result', 'output', 'response', 'status', 'error'}
    })


def _action_result(action: dict[str, Any]) -> str:
    for key in ('tool_result', 'result', 'output', 'response'):
        if key in action:
            return _json_text(action[key])
    return _json_text(action)


def _evidence_level(conversation: dict[str, Any]) -> str:
    actions, state = bool(conversation.get('action_trace')), bool(conversation.get('final_state'))
    return 'gray_box' if actions and state else 'partial_structured' if actions or state else 'black_box'


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _json_text(value: Any, *, limit: int = 10000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return text if len(text) <= limit else text[:limit] + f'\n[... truncated, {len(text)} chars total ...]'


def _identifier(value: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '-', value).strip('-._')
    return (cleaned if cleaned and cleaned[0].isalnum() else f'cae-{cleaned or "value"}')[:120]
