"""Offline scoring of human or explicitly provisional evaluation labels.

This module never calls an LLM. Model accuracy is not established by synthetic
fixtures or by comparing a fixture with a copy of its own expected labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

LABELS = frozenset({'pass', 'fail', 'not_observable', 'not_applicable'})
PREDICTIONS = LABELS | {'error'}
SPLITS = frozenset({'calibration', 'held_out'})


def measure_calibration(dataset: list[dict[str, Any]], predictions: list[dict[str, Any]],
                        *, split: str = 'held_out', require_human: bool = False) -> dict[str, Any]:
    """Compare normalized check outcomes, reporting abstentions and denominators.

    Records use case_id, conversation_group, split, label_source, and labels.
    Human records additionally require reviewer and label_version. Predictions
    use case_id and outcomes; missing predictions count as not_observable.
    """
    if not isinstance(dataset, list) or not isinstance(predictions, list):
        raise ValueError('Dataset and predictions must be JSON arrays')
    if split not in SPLITS:
        raise ValueError('split must be calibration or held_out')
    cases: dict[str, dict[str, Any]] = {}
    groups: dict[str, str] = {}
    for row in dataset:
        if not isinstance(row, dict):
            raise ValueError('Dataset records must be objects')
        case_id = _text(row.get('case_id'), 'case_id')
        if case_id in cases:
            raise ValueError(f'Duplicate case_id: {case_id}')
        group = _text(row.get('conversation_group'), 'conversation_group')
        row_split = row.get('split')
        if row_split not in SPLITS:
            raise ValueError(f'Invalid split for {case_id}')
        if group in groups and groups[group] != row_split:
            raise ValueError(f'Calibration/held-out conversation leakage: {group}')
        groups[group] = row_split
        labels = row.get('labels')
        if not isinstance(labels, dict) or not labels or any(
            not isinstance(k, str) or not k or v not in LABELS for k, v in labels.items()
        ):
            raise ValueError(f'Invalid normalized labels for {case_id}')
        source = row.get('label_source')
        if source not in {'human', 'synthetic_provisional'}:
            raise ValueError(f'Explicit label_source required for {case_id}')
        if source == 'human':
            _text(row.get('reviewer'), 'reviewer')
            _text(row.get('label_version'), 'label_version')
        cases[case_id] = row
    selected = {k: v for k, v in cases.items() if v['split'] == split}
    if not selected:
        raise ValueError(f'No cases in {split} split')
    all_human = all(row['label_source'] == 'human' for row in selected.values())
    if require_human and not all_human:
        raise ValueError('Human-approved labels are required; synthetic fixtures are not calibration evidence')
    results: dict[str, dict[str, str]] = {}
    for row in predictions:
        if not isinstance(row, dict):
            raise ValueError('Prediction records must be objects')
        case_id = _text(row.get('case_id'), 'case_id')
        if case_id not in cases or case_id in results:
            raise ValueError(f'Unknown or duplicate prediction case: {case_id}')
        outcomes = row.get('outcomes')
        if not isinstance(outcomes, dict) or any(
            key not in cases[case_id]['labels'] or value not in PREDICTIONS
            for key, value in outcomes.items()
        ):
            raise ValueError(f'Invalid normalized predictions for {case_id}')
        results[case_id] = outcomes
    per_check: dict[str, Counter] = defaultdict(Counter)
    total: Counter = Counter()
    overall: Counter = Counter()
    for case_id, row in selected.items():
        observed = results.get(case_id, {})
        for check, expected in row['labels'].items():
            predicted = observed.get(check, 'not_observable')
            _count(total, expected, predicted, missing=check not in observed)
            _count(per_check[check], expected, predicted, missing=check not in observed)
        _count(overall, _aggregate(row['labels'].values()),
               _aggregate(observed.get(check, 'not_observable') for check in row['labels']),
               missing=case_id not in results)
    return {
        'schema_version': 1, 'split': split, 'case_count': len(selected),
        'labels_human_approved': all_human,
        'interpretation': 'Human-labelled comparison; review coverage and error rates before release.' if all_human else
                          'Synthetic/provisional labels: harness validation only, not measured production judge accuracy.',
        'summary': _metrics(total), 'overall': _metrics(overall),
        'per_check': {key: _metrics(value) for key, value in sorted(per_check.items())},
    }


def _aggregate(values) -> str:
    values = set(values)
    # Evaluator failures are coverage failures, never proof of agent failure.
    if 'fail' in values:
        return 'fail'
    if 'error' in values:
        return 'error'
    if 'not_observable' in values:
        return 'not_observable'
    return 'pass' if 'pass' in values else 'not_applicable'


def _count(counts: Counter, expected: str, predicted: str, *, missing: bool) -> None:
    counts['total'] += 1
    counts['expected_' + expected] += 1
    counts['predicted_' + predicted] += 1
    counts['matched'] += expected == predicted
    counts['missing'] += missing
    counts['false_pass'] += expected == 'fail' and predicted == 'pass'
    counts['false_failure'] += expected == 'pass' and predicted == 'fail'
    counts['false_verification'] += expected == 'not_observable' and predicted == 'pass'
    counts['false_applicability'] += expected == 'not_applicable' and predicted in {'pass', 'fail'}
    counts['unresolved'] += predicted in {'not_observable', 'error'}
    if expected in {'pass', 'fail'} and predicted in {'pass', 'fail'}:
        counts['decisive_pairs'] += 1
        counts['decisive_matches'] += predicted == expected


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be non-empty text')
    return value.strip()


def _metrics(counts: Counter) -> dict[str, Any]:
    def ratio(numerator: str, denominator: str) -> float | None:
        return counts[numerator] / counts[denominator] if counts[denominator] else None
    keys = ('total', 'matched', 'missing', 'false_pass', 'false_failure', 'false_verification',
            'false_applicability', 'unresolved', 'decisive_pairs', 'decisive_matches',
            'expected_pass', 'expected_fail', 'expected_not_observable', 'expected_not_applicable',
            'predicted_pass', 'predicted_fail', 'predicted_not_observable', 'predicted_not_applicable', 'predicted_error')
    return {**{key: counts[key] for key in keys},
            'false_pass_rate': ratio('false_pass', 'expected_fail'),
            'false_failure_rate': ratio('false_failure', 'expected_pass'),
            'false_verification_rate': ratio('false_verification', 'expected_not_observable'),
            'unresolved_rate': ratio('unresolved', 'total'),
            'decisive_coverage': ratio('decisive_pairs', 'total'),
            'decisive_agreement': ratio('decisive_matches', 'decisive_pairs')}
