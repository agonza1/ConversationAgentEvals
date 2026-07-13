from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.schemas.assert_contracts import AssertRunCreateRequest
from app.schemas.benchmarks import BenchmarkRunRequest


EXAMPLE_ADAPTER_VERSION = 'conversation-agent-evals-acc-http-example-v1'
DEFAULT_ASSERT_SIDECAR_URL = 'http://127.0.0.1:8091'


def normalize_acc_run(payload: dict[str, Any], *, scenario: dict[str, Any]) -> dict[str, Any]:
    """Normalize one ACC end-to-end response into portable evaluation evidence.

    This example intentionally accepts the current scripted HTTP proof response. It
    does not claim live ASR, live TTS, full-duplex media, WebRTC, or SIP coverage.
    """

    call = _extract_call(payload)
    proof = _as_dict(payload.get('proof'))
    call_id = _first_text(
        call.get('session', {}).get('callId') if isinstance(call.get('session'), dict) else None,
        call.get('callId'),
        proof.get('callId'),
        payload.get('callId'),
    )
    transcript_turns = _normalize_transcript(call.get('transcript') or proof.get('transcript'))
    events = _normalize_events(call.get('events') or proof.get('events'))
    latency_marks = _normalize_latency(call.get('latencyMarks') or proof.get('latencyMarks'))
    outcome = _first_text(payload.get('outcome'), proof.get('outcome'), payload.get('summary')) or 'unknown'
    flow_state = _first_text(call.get('flowState'), proof.get('flowState'))
    complete = flow_state == 'wrap' or outcome in set(scenario.get('terminal_outcomes') or [])

    transcript_text = '\n'.join(
        f"{turn['speaker'].title()}: {turn['text']}" for turn in transcript_turns if turn.get('text')
    )
    action_trace = [
        {
            'step': index,
            'type': event['type'],
            'status': 'observed',
            'at': event.get('at'),
            'detail': deepcopy(event.get('detail') or {}),
        }
        for index, event in enumerate(events, start=1)
    ]
    final_state = {
        'complete': complete,
        'flow_state': flow_state,
        'outcome': outcome,
        'operator_steer': deepcopy(call.get('operatorSteer') or proof.get('operatorSteer') or {}),
        'fallback': deepcopy(call.get('demoFallback') or proof.get('demoFallback') or {}),
        'runtime_mode_labels': deepcopy(
            (call.get('session') or {}).get('runtimeModeLabels')
            if isinstance(call.get('session'), dict)
            else proof.get('runtimeModeLabels') or {}
        ),
    }
    limitations = list(scenario.get('current_mode', {}).get('limitations') or [])

    return {
        'schema': 'conversation_agent_evals_acc_evidence_v1',
        'generated_at': datetime.now(UTC).isoformat(),
        'adapter_version': EXAMPLE_ADAPTER_VERSION,
        'execution_mode': scenario.get('current_mode', {}).get('id', 'acc_http_scripted_fixture'),
        'scenario_id': scenario['scenario_id'],
        'call_id': call_id,
        'outcome': outcome,
        'transcript': transcript_text,
        'conversation': {'dialog': transcript_turns},
        'action_trace': action_trace,
        'final_state': final_state,
        'latency_evidence': {
            'marks': latency_marks,
            'within_budget': sum(1 for mark in latency_marks if _within_budget(mark) is True),
            'over_budget': sum(1 for mark in latency_marks if _within_budget(mark) is False),
        },
        'proof': proof or deepcopy(payload),
        'runtime_caveats': limitations,
        'provenance': {
            'source': 'agentic-contact-center',
            'source_repo': 'agonza1/agentic-contact-center',
            'target_endpoint': scenario.get('target', {}).get('run_endpoint'),
            'call_id': call_id,
            'execution_mode': scenario.get('current_mode', {}).get('id', 'acc_http_scripted_fixture'),
        },
    }


def build_benchmark_run_request(
    evidence: dict[str, Any],
    *,
    scenario: dict[str, Any],
    user_id: str = 'acc-example-user',
    project_id: str = 'agentic-contact-center',
) -> BenchmarkRunRequest:
    """Build the native benchmark request for the registered cancellation scenario."""

    return BenchmarkRunRequest.model_validate(
        {
            'suite_id': scenario['suite_id'],
            'scenario_id': scenario['scenario_id'],
            'transcript': evidence['transcript'],
            'conversation': evidence['conversation'],
            'action_trace': evidence['action_trace'],
            'final_state': evidence['final_state'],
            'assert_bundle': evidence,
            'notes': 'Optional ACC evidence evaluated by the native cancellation-rescue benchmark.',
            'metadata': {
                'execution_mode': evidence['execution_mode'],
                'adapter_version': evidence['adapter_version'],
                'runtime_caveats': evidence['runtime_caveats'],
                'provenance': evidence['provenance'],
                'scenario_contract': {
                    'required_actions': scenario.get('required_actions', []),
                    'forbidden_actions': scenario.get('forbidden_actions', []),
                    'expected_final_state': scenario.get('expected_final_state', {}),
                    'deterministic_checks': scenario.get('deterministic_checks', []),
                    'evidence_requirements': scenario.get('evidence_requirements', {}),
                },
                'benchmark_catalog_status': 'registered_native',
            },
            'user_id': user_id,
            'project_id': project_id,
        }
    )


def build_assert_run_request(
    evidence: dict[str, Any],
    *,
    scenario: dict[str, Any],
    assert_sidecar_url: str = DEFAULT_ASSERT_SIDECAR_URL,
    user_id: str = 'acc-example-user',
    project_id: str = 'agentic-contact-center',
) -> AssertRunCreateRequest:
    """Build and validate the canonical ASSERT wrapper request for the ACC example."""

    spec = scenario['spec_ref']
    run_label = f"{scenario['scenario_id']}:{evidence.get('call_id') or 'unknown-call'}"
    request = {
        'spec_ref': {
            'spec_id': spec['spec_id'],
            'spec_kind': spec.get('spec_kind', 'scenario'),
            'spec_version': spec.get('spec_version'),
            'spec_hash': spec.get('spec_hash'),
            'assert_project': spec.get('assert_project', 'conversation-agent-evals'),
            'assert_commit': spec.get('assert_commit'),
        },
        'evidence': {
            'transcript': _pointer('acc-transcript', 'transcript', evidence['transcript']),
            'conversation': _pointer('acc-conversation', 'conversation', evidence['conversation']),
            'action_trace': _pointer('acc-action-trace', 'action_trace', evidence['action_trace']),
            'final_state': _pointer('acc-final-state', 'final_state', evidence['final_state']),
            'assert_bundle': _pointer('acc-proof-bundle', 'assert_bundle', evidence),
            'additional_artifacts': [
                _pointer('acc-latency-evidence', 'report', evidence['latency_evidence']),
                _pointer(
                    'acc-runtime-caveats',
                    'report',
                    {
                        'execution_mode': evidence['execution_mode'],
                        'limitations': evidence['runtime_caveats'],
                        'claim': 'This Phase 1 example validates target integration and evidence ingestion, not live full-duplex audio.',
                    },
                ),
            ],
            'provenance': deepcopy(evidence['provenance']),
        },
        'runtime_config': {
            'execution_mode': 'async',
            'invocation_target': {
                'transport': 'http_sidecar',
                'environment': 'local',
                'base_url': assert_sidecar_url,
                'package_name': 'assert',
                'entrypoint': '/api/assert/runs',
                'timeout_seconds': 300,
            },
            'retry_policy': {'max_attempts': 1, 'retryable_statuses': ['error', 'failed']},
            'scenario_overrides': {
                'required_actions': scenario.get('required_actions', []),
                'forbidden_actions': scenario.get('forbidden_actions', []),
                'expected_final_state': scenario.get('expected_final_state', {}),
                'deterministic_checks': scenario.get('deterministic_checks', []),
                'evidence_requirements': scenario.get('evidence_requirements', {}),
            },
            'environment_labels': ['agentic-contact-center', evidence['execution_mode'], 'external-target-example'],
        },
        'platform_metadata': {
            'user_id': user_id,
            'project_id': project_id,
            'project_run_label': run_label,
            'initiated_by': 'agentic-contact-center-example',
            'notes': 'Optional scripted ACC HTTP target example; realtime audio remains a later transport mode.',
            'labels': ['acc-example', 'assert-ingestion', evidence['execution_mode']],
            'retention_days': 90,
            'billing_tags': {},
            'quota_scope': evidence.get('call_id'),
        },
    }
    return AssertRunCreateRequest.model_validate(request)


def _extract_call(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ('call', 'finalCall', 'snapshot'):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    proof = payload.get('proof')
    if isinstance(proof, dict):
        for key in ('call', 'snapshot', 'checkpoint'):
            value = proof.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _normalize_transcript(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = _first_text(item.get('text'), item.get('body'), item.get('transcript'))
        if not text:
            continue
        normalized.append(
            {
                'speaker': _first_text(item.get('speaker'), item.get('role')) or 'unknown',
                'text': text,
                'timestamp': _first_text(item.get('timestamp'), item.get('at'), item.get('created_at')),
            }
        )
    return normalized


def _normalize_events(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                'type': _first_text(item.get('type'), item.get('event_type')) or 'platform_event',
                'at': _first_text(item.get('at'), item.get('timestamp'), item.get('created_at')),
                'detail': deepcopy(_as_dict(item.get('detail') or item.get('payload'))),
            }
        )
    return normalized


def _normalize_latency(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                'stage': _first_text(item.get('stage')) or 'unknown',
                'elapsed_ms': _number(item.get('elapsedMs'), item.get('elapsed_ms')),
                'budget_ms': _number(item.get('budgetMs'), item.get('budget_ms')),
                'recorded_at': _first_text(item.get('recordedAt'), item.get('recorded_at'), item.get('at')),
            }
        )
    return normalized


def _pointer(artifact_id: str, kind: str, value: Any) -> dict[str, Any]:
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)
    return {
        'artifact_id': artifact_id,
        'kind': kind,
        'role': 'input',
        'inline_data': deepcopy(value),
        'mime_type': 'application/json' if isinstance(value, (dict, list)) else 'text/plain',
        'sha256': hashlib.sha256(encoded.encode('utf-8')).hexdigest(),
        'size_bytes': len(encoded.encode('utf-8')),
        'source': EXAMPLE_ADAPTER_VERSION,
        'readiness': 'ready',
        'metadata': {'adapter_version': EXAMPLE_ADAPTER_VERSION},
    }


def _within_budget(mark: dict[str, Any]) -> bool | None:
    elapsed = mark.get('elapsed_ms')
    budget = mark.get('budget_ms')
    if not isinstance(elapsed, (int, float)) or not isinstance(budget, (int, float)):
        return None
    return elapsed <= budget


def _number(*values: Any) -> int | float | None:
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
