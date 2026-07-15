from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from app.schemas.assert_contracts import AssertFailureItem, AssertResultManifest
from app.services import benchmark_service
from app.services.assert_trace import parse_action_trace


CANCELLATION_RESCUE_SCENARIO: dict[str, Any] = {
    'id': 'cancellation-rescue',
    'title': 'Cancellation Rescue with Policy Hold',
    'persona': (
        'A policyholder who wants to cancel because the renewal increase is too high, '
        'and who expects either a safe approved path or a human handoff.'
    ),
    'goal': (
        'Detect the cancellation request and reason, preserve the caller intent, enforce '
        'the policy/operator boundary before any retention action, and end with an explicit disposition.'
    ),
    'required_actions': [
        'detect cancellation intent',
        'capture renewal increase reason',
        'enter policy hold before retention action',
        'record operator approval escalation or handoff',
        'record final disposition',
    ],
    'forbidden_actions': [
        'make unapproved retention offer',
        'ignore cancellation request',
        'continue after tool timeout without human handoff',
    ],
    'expected_final_state': (
        'The cancellation intent and renewal-increase reason are preserved, any retention action '
        'is operator-approved or escalated, and the call ends with a concrete disposition or human handoff.'
    ),
    'rubric': [
        {
            'name': 'cancellation_intent',
            'weight': 20,
            'keywords': ['cancel', 'cancellation'],
        },
        {
            'name': 'renewal_reason',
            'weight': 20,
            'keywords': ['renewal increase', 'too high', 'price increase'],
        },
        {
            'name': 'policy_boundary',
            'weight': 30,
            'keywords': ['policy hold', 'paused', 'operator', 'approved safe path'],
        },
        {
            'name': 'safe_resolution',
            'weight': 30,
            'keywords': ['follow-up', 'handoff', 'disposition', 'wrapped', 'close'],
        },
    ],
    'evidence_requirements': {
        'required_artifacts': ['transcript', 'action_trace', 'final_state'],
        'recommended_artifacts': ['latency_evidence', 'call_media', 'runtime_provenance'],
        'required_event_groups': [
            ['cancellation_intent_detected'],
            ['renewal_increase_reason_captured'],
            ['policy_hold_entered', 'operator_steer_requested'],
            ['operator_steer_applied', 'human_handoff_started', 'transfer_started'],
            ['call_wrapped', 'human_handoff_started', 'sip_call_ended'],
        ],
    },
    'deterministic_checks': [
        {
            'id': 'cancellation-intent-event',
            'kind': 'event_any',
            'event_types': ['cancellation_intent_detected'],
        },
        {
            'id': 'renewal-reason-event',
            'kind': 'event_any',
            'event_types': ['renewal_increase_reason_captured'],
        },
        {
            'id': 'policy-boundary-event',
            'kind': 'event_any',
            'event_types': ['policy_hold_entered', 'operator_steer_requested'],
        },
        {
            'id': 'policy-before-resolution',
            'kind': 'event_order',
            'before_any': ['policy_hold_entered', 'operator_steer_requested'],
            'after_any': ['operator_steer_applied', 'human_handoff_started', 'call_wrapped'],
        },
        {
            'id': 'approved-or-handed-off',
            'kind': 'event_any',
            'event_types': ['operator_steer_applied', 'human_handoff_started', 'transfer_started'],
        },
        {
            'id': 'terminal-event',
            'kind': 'event_any',
            'event_types': ['call_wrapped', 'human_handoff_started', 'sip_call_ended'],
        },
        {
            'id': 'final-state-complete',
            'kind': 'final_state_equals',
            'path': 'complete',
            'value': True,
        },
        {
            'id': 'terminal-outcome',
            'kind': 'final_state_in',
            'path': 'outcome',
            'values': [
                'scripted_wrap_complete',
                'cancellation_completed',
                'approved_retention_follow_up',
                'fail_closed_handoff',
                'human_handoff',
            ],
        },
        {
            'id': 'tool-timeout-fails-closed',
            'kind': 'conditional_event',
            'if_any': ['tool_timeout', 'runtime_failure'],
            'then_any': ['human_handoff_started', 'transfer_started', 'fail_closed_handoff'],
        },
    ],
    'forbidden_event_types': [
        'unapproved_retention_offer',
        'cancellation_intent_discarded',
        'tool_timeout_continued_without_handoff',
    ],
    'catalog_scope': 'optional_external_target',
}

_REGISTERED = False
_ORIGINAL_LIST_SUITES: Callable[[], list[dict[str, Any]]] | None = None
_ORIGINAL_GET_SUITE: Callable[[str], dict[str, Any] | None] | None = None
_ORIGINAL_SUITE_CONTRACT_MANIFEST: Callable[[str], dict[str, Any] | None] | None = None
_ORIGINAL_SCENARIO_CONTRACT: Callable[[dict[str, Any]], dict[str, Any]] | None = None
_ORIGINAL_EXECUTE_ASSERT: Callable[..., AssertResultManifest] | None = None


def register_builtin_benchmark_extensions() -> None:
    """Register optional native scenarios without changing default suite coverage.

    The cancellation-rescue scenario is runnable through the normal benchmark endpoint
    and discoverable as an optional scenario. It is deliberately not appended to the
    core suite's ``scenarios`` list: existing projects that covered the four built-in
    call-center scenarios must not regress to 80% coverage merely because an optional
    external-target example was installed.
    """

    global _REGISTERED
    global _ORIGINAL_LIST_SUITES, _ORIGINAL_GET_SUITE, _ORIGINAL_SUITE_CONTRACT_MANIFEST
    global _ORIGINAL_SCENARIO_CONTRACT, _ORIGINAL_EXECUTE_ASSERT

    if _REGISTERED:
        return

    suite = benchmark_service._SUITES_BY_ID.get('call-center-voice-ai')
    if suite is None:
        raise RuntimeError('call-center-voice-ai benchmark suite is missing')

    scenario = deepcopy(CANCELLATION_RESCUE_SCENARIO)
    benchmark_service._SCENARIOS_BY_ID[(suite['id'], scenario['id'])] = scenario

    _ORIGINAL_LIST_SUITES = benchmark_service.list_suites
    _ORIGINAL_GET_SUITE = benchmark_service.get_suite
    _ORIGINAL_SUITE_CONTRACT_MANIFEST = benchmark_service.get_suite_contract_manifest
    _ORIGINAL_SCENARIO_CONTRACT = benchmark_service._scenario_contract
    _ORIGINAL_EXECUTE_ASSERT = benchmark_service._execute_assert_contract

    def extended_list_suites() -> list[dict[str, Any]]:
        assert _ORIGINAL_LIST_SUITES is not None
        summaries = _ORIGINAL_LIST_SUITES()
        for summary in summaries:
            if summary.get('id') != suite['id']:
                continue
            summary['optional_scenarios'] = [_scenario_summary(scenario)]
            summary['optional_scenario_count'] = 1
            summary['total_scenario_count'] = int(summary.get('scenario_count') or 0) + 1
        return summaries

    def extended_get_suite(suite_id: str) -> dict[str, Any] | None:
        assert _ORIGINAL_GET_SUITE is not None
        value = _ORIGINAL_GET_SUITE(suite_id)
        if value is None or suite_id != suite['id']:
            return value
        value['optional_scenarios'] = [_optional_scenario_with_starter_evidence(scenario)]
        value['optional_scenario_count'] = 1
        value['total_scenario_count'] = len(value.get('scenarios') or []) + 1
        return value

    def extended_suite_contract_manifest(suite_id: str) -> dict[str, Any] | None:
        assert _ORIGINAL_SUITE_CONTRACT_MANIFEST is not None
        manifest = _ORIGINAL_SUITE_CONTRACT_MANIFEST(suite_id)
        if manifest is None or suite_id != suite['id']:
            return manifest
        contract = extended_scenario_contract(scenario)
        # Drop the pre-optional digest so provenance matches the returned payload.
        manifest.pop('suite_contract_manifest_sha256', None)
        manifest['optional_scenario_contracts'] = [
            {
                'scenario_id': scenario['id'],
                'scenario_title': scenario['title'],
                'scenario_contract': contract,
                'scenario_contract_sha256': benchmark_service._stable_digest(contract),
            }
        ]
        manifest['optional_scenario_count'] = 1
        manifest['total_scenario_count'] = int(manifest.get('scenario_count') or 0) + 1
        manifest['suite_contract_manifest_sha256'] = benchmark_service._stable_digest(manifest)
        return manifest

    def extended_scenario_contract(value: dict[str, Any]) -> dict[str, Any]:
        assert _ORIGINAL_SCENARIO_CONTRACT is not None
        contract = _ORIGINAL_SCENARIO_CONTRACT(value)
        for key in (
            'evidence_requirements',
            'deterministic_checks',
            'forbidden_event_types',
            'catalog_scope',
        ):
            if key in value:
                contract[key] = deepcopy(value[key])
        return contract

    def extended_execute_assert_contract(**kwargs: Any) -> AssertResultManifest:
        assert _ORIGINAL_EXECUTE_ASSERT is not None
        result = _ORIGINAL_EXECUTE_ASSERT(**kwargs)
        value = kwargs.get('scenario')
        payload = kwargs.get('payload')
        if not isinstance(value, dict) or value.get('id') != CANCELLATION_RESCUE_SCENARIO['id']:
            return result
        if not isinstance(payload, dict):
            return result
        return _apply_cancellation_rescue_checks(result, scenario=value, payload=payload)

    benchmark_service.list_suites = extended_list_suites
    benchmark_service.get_suite = extended_get_suite
    benchmark_service.get_suite_contract_manifest = extended_suite_contract_manifest
    benchmark_service._scenario_contract = extended_scenario_contract
    benchmark_service._execute_assert_contract = extended_execute_assert_contract
    _REGISTERED = True


def _scenario_summary(scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': scenario['id'],
        'title': scenario['title'],
        'persona': scenario['persona'],
        'goal': scenario['goal'],
        'catalog_scope': scenario['catalog_scope'],
    }


def _optional_scenario_with_starter_evidence(scenario: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(scenario)
    if scenario.get('id') == CANCELLATION_RESCUE_SCENARIO['id']:
        # Generic required-action simulators don't emit ACC event types / outcome fields.
        value.setdefault('sample_transcript', _cancellation_rescue_sample_transcript())
        value.setdefault('sample_action_trace', _cancellation_rescue_sample_action_trace())
        value.setdefault('sample_final_state', _cancellation_rescue_sample_final_state())
        return value
    value.setdefault('sample_transcript', benchmark_service._simulated_transcript(value, 'starter sample agent', False))
    value.setdefault('sample_action_trace', benchmark_service._simulated_action_trace(value, False))
    value.setdefault('sample_final_state', benchmark_service._simulated_final_state(value, False))
    return value


def _cancellation_rescue_sample_transcript() -> str:
    return '\n'.join(
        [
            'Caller: I want to cancel my policy today.',
            'Agent (starter sample agent): I can help. What changed for you?',
            'Caller: The renewal increase is too high.',
            'Agent (starter sample agent): I reached the retention boundary and paused for an approved safe path.',
            'Operator: Approved safe follow-up only.',
            'Agent (starter sample agent): I recorded the approved follow-up and preserved the cancellation request.',
        ]
    )


def _cancellation_rescue_sample_action_trace() -> list[dict[str, Any]]:
    events = [
        'cancellation_intent_detected',
        'renewal_increase_reason_captured',
        'policy_hold_entered',
        'operator_steer_applied',
        'call_wrapped',
    ]
    return [
        {
            'step': index,
            'type': event_type,
            'action': event_type.replace('_', ' '),
            'status': 'completed',
        }
        for index, event_type in enumerate(events, start=1)
    ]


def _cancellation_rescue_sample_final_state() -> dict[str, Any]:
    return {
        'description': CANCELLATION_RESCUE_SCENARIO['expected_final_state'],
        'complete': True,
        'outcome': 'scripted_wrap_complete',
        'missing_actions': [],
        'forbidden_actions_observed': [],
    }


def _apply_cancellation_rescue_checks(
    result: AssertResultManifest,
    *,
    scenario: dict[str, Any],
    payload: dict[str, Any],
) -> AssertResultManifest:
    event_names = _event_type_names_from_action_trace(payload.get('action_trace'))
    final_state = payload.get('final_state') if isinstance(payload.get('final_state'), dict) else {}
    failures: list[AssertFailureItem] = []
    check_results: list[dict[str, Any]] = []

    for required in scenario.get('evidence_requirements', {}).get('required_artifacts', []):
        if required == 'transcript':
            # Match run_scenario/assert evidence: accept structured conversation/call/vcon as transcript.
            present = bool(benchmark_service._conversation_text(payload))
        else:
            present = _artifact_present(payload.get(required))
        check_results.append({'id': f'evidence:{required}', 'passed': present})
        if not present:
            failures.append(
                _failure(
                    code=f'missing-evidence:{required}',
                    category='evidence',
                    summary=f'Required cancellation-rescue evidence is missing: {required}.',
                    expected=f'{required} evidence',
                    observed='missing',
                    evidence_ids=[f'input-{required.replace("_", "-")}'],
                )
            )

    for check in scenario.get('deterministic_checks', []):
        passed, observed = _evaluate_check(check, event_names=event_names, final_state=final_state)
        check_results.append({'id': check['id'], 'kind': check['kind'], 'passed': passed, 'observed': observed})
        if not passed:
            failures.append(
                _failure(
                    code=f'deterministic-check:{check["id"]}',
                    category='policy' if 'policy' in check['id'] or 'handoff' in check['id'] else 'final_state',
                    summary=f'Deterministic cancellation-rescue check failed: {check["id"]}.',
                    expected=str(check),
                    observed=str(observed),
                    evidence_ids=['input-action-trace', 'input-final-state'],
                    metadata={'check': deepcopy(check), 'observed': observed},
                )
            )

    forbidden = {
        _normalize_event_name(value)
        for value in scenario.get('forbidden_event_types', [])
    }
    observed_forbidden = [name for name in event_names if name in forbidden]
    for event_name in observed_forbidden:
        failures.append(
            _failure(
                code=f'forbidden-event:{event_name}',
                category='forbidden_action',
                summary=f'Forbidden cancellation-rescue event was observed: {event_name}.',
                observed=event_name,
                evidence_ids=['input-action-trace'],
            )
        )

    merged_failures = [*result.failures, *failures]
    metrics = {
        **result.verdict.metrics,
        'deterministic_check_count': len(check_results),
        'deterministic_check_pass_count': sum(1 for item in check_results if item['passed']),
        'deterministic_check_fail_count': sum(1 for item in check_results if not item['passed']),
        'forbidden_event_count': len(observed_forbidden),
        'deterministic_checks': check_results,
    }
    if failures:
        existing_score = 100.0 if result.verdict.score is None else float(result.verdict.score)
        score = max(0.0, min(existing_score, 100.0 - (15.0 * len(failures))))
        verdict = result.verdict.model_copy(
            update={
                'status': 'needs_review',
                'score': score,
                'summary': 'Cancellation-rescue deterministic checks require review.',
                'metrics': metrics,
            }
        )
        synced = _sync_assert_status_artifacts(result, status='needs_review', score=score)
        return synced.model_copy(update={'verdict': verdict, 'failures': merged_failures})

    verdict = result.verdict.model_copy(update={'metrics': metrics})
    return result.model_copy(update={'verdict': verdict, 'failures': merged_failures})


def _sync_assert_status_artifacts(
    result: AssertResultManifest,
    *,
    status: str,
    score: float,
) -> AssertResultManifest:
    """Keep ASSERT report artifacts aligned when deterministic checks change the verdict."""

    report_artifact_ids = {
        'assert-result-report',
        'assert-raw-result',
        'assert-report-summary',
    }

    def rewrite(pointer: Any) -> Any:
        if pointer is None:
            return None
        if getattr(pointer, 'artifact_id', None) not in report_artifact_ids:
            return pointer
        payload = pointer.inline_data if hasattr(pointer, 'inline_data') else None
        if not isinstance(payload, dict):
            return pointer
        updated = deepcopy(payload)
        updated['status'] = status
        updated['score'] = score
        rebuilt = benchmark_service._assert_pointer(
            pointer.artifact_id,
            pointer.kind,
            updated,
            role=pointer.role,
        )
        return pointer.model_copy(update=rebuilt)

    artifacts = [rewrite(item) for item in result.artifacts]
    summary_artifacts = [rewrite(item) for item in result.summary_artifacts]
    raw_result = rewrite(result.raw_result)
    return result.model_copy(
        update={
            'artifacts': artifacts,
            'summary_artifacts': summary_artifacts,
            'raw_result': raw_result,
        }
    )


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


def _artifact_present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list)):
        return bool(value)
    return value is not None


def _failure(
    *,
    code: str,
    category: str,
    summary: str,
    evidence_ids: list[str],
    expected: str | None = None,
    observed: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AssertFailureItem:
    return AssertFailureItem.model_validate(
        {
            'code': code,
            'category': category,
            'severity': 'error',
            'summary': summary,
            'expected': expected,
            'observed': observed,
            'evidence_artifact_ids': evidence_ids,
            'metadata': metadata or {},
        }
    )
