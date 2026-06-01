from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MISSING = object()


ACTION_NAME_KEYS = ('name', 'action', 'tool', 'tool_name', 'function', 'operation', 'type')
ACTION_ARGS_KEYS = ('arguments', 'args', 'input', 'inputs', 'parameters', 'params', 'payload')
ACTION_RESULT_KEYS = ('result', 'output', 'outputs', 'response')
ACTION_STATUS_KEYS = ('status', 'state', 'outcome')
SUCCESS_VALUES = {'complete', 'completed', 'done', 'success', 'succeeded', 'passed', 'pass', True}
FAILURE_VALUES = {'fail', 'failed', 'failure', 'error', 'errored', 'cancelled', 'canceled', False}


@dataclass(frozen=True)
class ActionEvent:
    name: str
    arguments: dict[str, Any]
    result: Any = None
    status: str | None = None
    raw: Any = None


@dataclass(frozen=True)
class ScoreResult:
    score: int
    passed: bool
    total: int
    matched: int
    missing: list[Any]
    violations: list[Any]
    evidence: list[Any]


@dataclass(frozen=True)
class BenchmarkEvaluation:
    task_completion: ScoreResult
    required_action_execution: ScoreResult
    forbidden_action_avoidance: ScoreResult
    final_state_correctness: ScoreResult

    @property
    def overall_score(self) -> int:
        scores = [
            self.task_completion.score,
            self.required_action_execution.score,
            self.forbidden_action_avoidance.score,
            self.final_state_correctness.score,
        ]
        return round(sum(scores) / len(scores))


def parse_action_trace(action_trace: Any) -> list[ActionEvent]:
    """Normalize common action/tool trace shapes into ordered action events."""
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


def parse_final_state(final_state: Any) -> dict[str, Any]:
    if isinstance(final_state, dict):
        return final_state
    return {}


def score_required_action_execution(action_trace: Any, required_actions: list[Any] | None) -> ScoreResult:
    actions = parse_action_trace(action_trace)
    requirements = required_actions or []
    if not requirements:
        return _score(100, True, 0, 0, [], [], [])

    evidence: list[Any] = []
    missing: list[Any] = []
    for requirement in requirements:
        match = _first_matching_required_action(actions, requirement)
        if match:
            evidence.append(_event_evidence(match))
        else:
            missing.append(requirement)

    matched = len(requirements) - len(missing)
    score = round((matched / len(requirements)) * 100)
    return _score(score, matched == len(requirements), len(requirements), matched, missing, [], evidence)


def score_forbidden_action_avoidance(action_trace: Any, forbidden_actions: list[Any] | None) -> ScoreResult:
    actions = parse_action_trace(action_trace)
    forbidden = forbidden_actions or []
    if not forbidden:
        return _score(100, True, 0, 0, [], [], [])

    violations: list[Any] = []
    for action in actions:
        for requirement in forbidden:
            if _action_matches(action, requirement):
                violations.append(_event_evidence(action))
                break

    score = 100 if not violations else 0
    return _score(score, not violations, len(forbidden), len(forbidden) - len(violations), [], violations, [])


def score_final_state_correctness(final_state: Any, expected_final_state: Any | None) -> ScoreResult:
    state = parse_final_state(final_state)
    assertions = _normalize_state_assertions(expected_final_state)
    if not assertions:
        return _score(100, True, 0, 0, [], [], [])

    evidence: list[Any] = []
    missing: list[Any] = []
    for assertion in assertions:
        actual = _read_path(state, assertion['path'])
        if actual is not MISSING and _value_matches(actual, assertion['expected']):
            evidence.append({'path': assertion['path'], 'actual': actual})
        else:
            missing.append({'path': assertion['path'], 'expected': assertion['expected'], 'actual': None if actual is MISSING else actual})

    matched = len(assertions) - len(missing)
    score = round((matched / len(assertions)) * 100)
    return _score(score, matched == len(assertions), len(assertions), matched, missing, [], evidence)


def score_task_completion(action_trace: Any, final_state: Any, completion_criteria: Any | None = None) -> ScoreResult:
    state = parse_final_state(final_state)
    criteria = completion_criteria or {}
    if isinstance(criteria, dict):
        expected_state = criteria.get('final_state') or criteria.get('expected_final_state') or criteria.get('state')
        if expected_state:
            return score_final_state_correctness(state, expected_state)

        required_actions = criteria.get('required_actions')
        if required_actions:
            return score_required_action_execution(action_trace, required_actions)

        expected = criteria.get('completed', criteria.get('success', MISSING))
        if expected is not MISSING:
            observed = _completion_value(state)
            expected_completed = _expected_completion_value(expected)
            observed_completed = _truthy_completion(observed)
            passed = observed_completed == expected_completed
            return _score(100 if passed else 0, passed, 1, 1 if passed else 0, [] if passed else [{'expected': expected, 'actual': observed}], [], [{'actual': observed}] if passed else [])

    observed = _completion_value(state)
    if observed is MISSING:
        return _score(0, False, 1, 0, [{'expected': 'completed final state', 'actual': None}], [], [])

    passed = _truthy_completion(observed)
    return _score(100 if passed else 0, passed, 1, 1 if passed else 0, [] if passed else [{'expected': 'completed', 'actual': observed}], [], [{'actual': observed}] if passed else [])


def evaluate_benchmark(
    action_trace: Any,
    final_state: Any,
    *,
    task_completion: Any | None = None,
    required_actions: list[Any] | None = None,
    forbidden_actions: list[Any] | None = None,
    expected_final_state: Any | None = None,
) -> BenchmarkEvaluation:
    return BenchmarkEvaluation(
        task_completion=score_task_completion(action_trace, final_state, task_completion),
        required_action_execution=score_required_action_execution(action_trace, required_actions),
        forbidden_action_avoidance=score_forbidden_action_avoidance(action_trace, forbidden_actions),
        final_state_correctness=score_final_state_correctness(final_state, expected_final_state),
    )


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


def _first_matching_action(actions: list[ActionEvent], requirement: Any) -> ActionEvent | None:
    for action in actions:
        if _action_matches(action, requirement):
            return action
    return None


def _first_matching_required_action(actions: list[ActionEvent], requirement: Any) -> ActionEvent | None:
    for action in actions:
        if _action_matches(action, requirement) and _action_has_successful_status(action, requirement):
            return action
    return None


def _action_matches(action: ActionEvent, requirement: Any) -> bool:
    if isinstance(requirement, str):
        return _normalize_name(action.name) == _normalize_name(requirement)

    if not isinstance(requirement, dict):
        return False

    one_of = requirement.get('one_of') or requirement.get('any_of')
    if isinstance(one_of, list):
        return any(_action_matches(action, candidate) for candidate in one_of)

    expected_name = _first_present(requirement, ACTION_NAME_KEYS)
    if expected_name is not None and _normalize_name(action.name) != _normalize_name(str(expected_name)):
        return False

    expected_args = requirement.get('arguments') or requirement.get('args') or requirement.get('input') or requirement.get('parameters')
    if expected_args is not None and not _value_matches(action.arguments, expected_args):
        return False

    expected_status = requirement.get('status') or requirement.get('state') or requirement.get('outcome')
    if expected_status is not None and _normalize_name(action.status or '') != _normalize_name(str(expected_status)):
        return False

    return expected_name is not None or expected_args is not None or expected_status is not None


def _action_has_successful_status(action: ActionEvent, requirement: Any) -> bool:
    if isinstance(requirement, dict):
        expected_status = requirement.get('status') or requirement.get('state') or requirement.get('outcome')
        if expected_status is not None:
            return True

    if action.status is None:
        return True

    normalized_status = _normalized_status_value(action.status)
    return normalized_status not in {_normalized_status_value(value) for value in FAILURE_VALUES}


def _normalized_status_value(value: Any) -> str:
    return str(value).strip().lower().replace('-', '_').replace(' ', '_')


def _normalize_name(value: str) -> str:
    return value.strip().lower().replace('-', '_').replace(' ', '_')


def _value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, expected_value in expected.items():
            actual_value = _read_path(actual, key)
            if actual_value is MISSING or not _value_matches(actual_value, expected_value):
                return False
        return True

    if isinstance(expected, list):
        return actual == expected

    return actual == expected


def _normalize_state_assertions(expected_final_state: Any | None) -> list[dict[str, Any]]:
    if not expected_final_state:
        return []

    if isinstance(expected_final_state, list):
        assertions: list[dict[str, Any]] = []
        for item in expected_final_state:
            if isinstance(item, dict) and 'path' in item:
                assertions.append({'path': str(item['path']), 'expected': item.get('equals', item.get('expected'))})
        return assertions

    if isinstance(expected_final_state, dict):
        return [{'path': str(key), 'expected': value} for key, value in expected_final_state.items()]

    return []


def _read_path(state: Any, path: str) -> Any:
    current = state
    for part in path.split('.'):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return MISSING
    return current


def _completion_value(state: dict[str, Any]) -> Any:
    for path in ('task_completion', 'complete', 'completed', 'success', 'status', 'state', 'outcome', 'result.status'):
        value = _read_path(state, path)
        if value is not MISSING:
            return value
    return MISSING


def _truthy_completion(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in SUCCESS_VALUES
    return value in SUCCESS_VALUES


def _expected_completion_value(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in FAILURE_VALUES:
            return False
        return normalized in SUCCESS_VALUES
    return value in SUCCESS_VALUES


def _event_evidence(action: ActionEvent) -> dict[str, Any]:
    evidence: dict[str, Any] = {'name': action.name}
    if action.arguments:
        evidence['arguments'] = action.arguments
    if action.status is not None:
        evidence['status'] = action.status
    return evidence


def _score(
    score: int,
    passed: bool,
    total: int,
    matched: int,
    missing: list[Any],
    violations: list[Any],
    evidence: list[Any],
) -> ScoreResult:
    return ScoreResult(
        score=score,
        passed=passed,
        total=total,
        matched=matched,
        missing=missing,
        violations=violations,
        evidence=evidence,
    )
