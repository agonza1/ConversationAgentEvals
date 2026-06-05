from __future__ import annotations

from copy import deepcopy
from typing import Any


ASSERT_ADAPTER_VERSION = 'assert_style_v1'


def normalize_assert_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    raw_bundle = payload.get('assert_bundle') or payload.get('assertBundle')
    if not isinstance(raw_bundle, dict) or not raw_bundle:
        return payload, None

    normalized = deepcopy(payload)
    bundle = deepcopy(raw_bundle)
    normalized['assert_bundle'] = deepcopy(raw_bundle)
    summary: dict[str, Any] = {
        'name': ASSERT_ADAPTER_VERSION,
        'source_artifacts': [],
        'normalized_artifacts': [],
        'input_keys': sorted(str(key) for key in bundle.keys()),
    }

    if not _has_direct_conversation_payload(payload):
        conversation_key, conversation_value = _extract_conversation_artifact(bundle)
        if conversation_key and conversation_value is not None:
            normalized_artifact_key = _apply_conversation_artifact(normalized, conversation_key, conversation_value)
            if normalized_artifact_key:
                summary['source_artifacts'].append(conversation_key)
                summary['normalized_artifacts'].append(normalized_artifact_key)

    if not _artifact_present(payload.get('action_trace')):
        action_trace_key, action_trace_value = _extract_first_artifact(
            bundle,
            'action_trace',
            'tool_trace',
            'tool_log',
            'trace',
            'tool_calls',
            'actions',
            'events',
            'steps',
        )
        if action_trace_key and _artifact_present(action_trace_value):
            normalized['action_trace'] = deepcopy(action_trace_value)
            summary['source_artifacts'].append(action_trace_key)
            summary['normalized_artifacts'].append('action_trace')

    if not _artifact_present(payload.get('final_state')):
        final_state_key, final_state_value = _extract_first_artifact(
            bundle,
            'final_state',
            'finalState',
            'task_state',
            'state',
            'result',
            'outcome',
        )
        if final_state_key and _artifact_present(final_state_value):
            normalized['final_state'] = deepcopy(final_state_value)
            summary['source_artifacts'].append(final_state_key)
            summary['normalized_artifacts'].append('final_state')

    extracted_metadata = _extract_metadata(bundle)
    if extracted_metadata:
        existing_metadata = normalized.get('metadata') if isinstance(normalized.get('metadata'), dict) else {}
        normalized['metadata'] = {**extracted_metadata, **existing_metadata}
        summary['metadata_labels'] = sorted(str(key) for key in normalized['metadata'].keys())

    provenance = _extract_provenance(bundle)
    if provenance:
        summary['provenance'] = provenance

    normalized['assert_adapter'] = summary
    return normalized, summary


def _has_direct_conversation_payload(payload: dict[str, Any]) -> bool:
    return any(_artifact_present(payload.get(key)) for key in ('transcript', 'conversation', 'call', 'group_call', 'groupCall', 'vcon'))


def _extract_conversation_artifact(bundle: dict[str, Any]) -> tuple[str | None, Any]:
    if isinstance(bundle.get('vcon'), dict):
        return 'vcon', bundle.get('vcon')

    if isinstance(bundle.get('vcon'), str) and isinstance(bundle.get('dialog'), list):
        return 'vcon', bundle

    for key in ('transcript', 'conversation_text', 'conversationText'):
        value = bundle.get(key)
        if isinstance(value, str) and value.strip():
            return key, value

    conversation = bundle.get('conversation')
    if _artifact_present(conversation):
        return 'conversation', conversation

    for key in ('dialog', 'messages', 'turns', 'utterances'):
        value = bundle.get(key)
        if isinstance(value, list) and value:
            return key, value

    return None, None


def _apply_conversation_artifact(payload: dict[str, Any], source_key: str, value: Any) -> str | None:
    if source_key == 'vcon' and isinstance(value, dict):
        payload['vcon'] = deepcopy(value)
        return 'vcon'

    if source_key in {'transcript', 'conversation_text', 'conversationText'} and isinstance(value, str):
        payload['transcript'] = value.strip()
        return 'transcript'

    if source_key == 'conversation':
        payload['conversation'] = deepcopy(value)
        return 'conversation'

    if source_key in {'dialog', 'messages', 'turns', 'utterances'} and isinstance(value, list):
        payload['conversation'] = {'dialog': deepcopy(value)}
        return 'conversation'

    return None


def _extract_first_artifact(bundle: dict[str, Any], *keys: str) -> tuple[str | None, Any]:
    for key in keys:
        value = bundle.get(key)
        if _artifact_present(value):
            return key, value
    return None, None


def _extract_metadata(bundle: dict[str, Any]) -> dict[str, Any]:
    metadata = bundle.get('metadata') if isinstance(bundle.get('metadata'), dict) else {}
    run_metadata = bundle.get('run_metadata') if isinstance(bundle.get('run_metadata'), dict) else {}
    context = bundle.get('context') if isinstance(bundle.get('context'), dict) else {}

    combined = {**metadata, **run_metadata, **context}
    for key in ('agent_version', 'prompt_version', 'model_name', 'notes', 'user_id', 'project_id'):
        value = bundle.get(key)
        if isinstance(value, str) and value.strip():
            combined[key] = value.strip()

    return combined


def _extract_provenance(bundle: dict[str, Any]) -> dict[str, Any]:
    provenance = bundle.get('provenance') if isinstance(bundle.get('provenance'), dict) else {}
    combined = dict(provenance)
    for key in ('source_run_id', 'source_failure_id', 'production_failure_id', 'incident_id'):
        value = bundle.get(key)
        if isinstance(value, str) and value.strip():
            combined[key] = value.strip()
    return combined


def _artifact_present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return value is not None
