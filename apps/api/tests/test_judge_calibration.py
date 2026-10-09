"""Harness arithmetic tests, not a benchmark of real judge accuracy."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from app.services.judge_calibration import measure_calibration


def case(case_id, label, **kwargs):
    return {'case_id': case_id, 'conversation_group': case_id, 'split': 'held_out',
            'label_source': 'synthetic_provisional', 'labels': {'behavior': label}, **kwargs}


def test_error_rates_include_abstentions_and_denominators():
    dataset = [case('a', 'fail'), case('b', 'fail'), case('c', 'pass'),
               case('d', 'not_observable'), case('e', 'not_applicable'), case('f', 'pass')]
    predictions = [{'case_id': key, 'outcomes': {'behavior': value}} for key, value in
                   [('a', 'pass'), ('b', 'not_observable'), ('c', 'fail'), ('d', 'pass'), ('e', 'not_applicable'), ('f', 'error')]]
    result = measure_calibration(dataset, predictions)
    for summary in (result['summary'], result['overall'], result['per_check']['behavior']):
        assert summary['false_pass'] == 1 and summary['false_pass_rate'] == .5
        assert summary['false_failure'] == 1 and summary['false_failure_rate'] == .5
        assert summary['false_verification_rate'] == 1
        assert summary['unresolved'] == 2 and summary['unresolved_rate'] == 2/6
        assert summary['decisive_coverage'] == 2/6 and summary['decisive_agreement'] == 0
    assert result['labels_human_approved'] is False
    assert 'not measured production' in result['interpretation']


def test_missing_predictions_count_as_coverage_gaps_not_failed_agents():
    result = measure_calibration([case('a', 'pass')], [])['summary']
    assert result['missing'] == 1 and result['unresolved'] == 1
    assert result['false_failure'] == 0 and result['decisive_agreement'] is None


def test_overall_does_not_average_away_critical_failure():
    dataset = [case('a', 'pass', labels={'identity': 'pass', 'authorization': 'fail'})]
    result = measure_calibration(dataset, [{'case_id': 'a', 'outcomes': {'identity': 'pass', 'authorization': 'pass'}}])
    assert result['overall']['false_pass_rate'] == 1
    assert result['summary']['matched'] == 1


def test_synthetic_fixtures_are_explicit_and_cannot_be_human_calibration():
    path = Path(__file__).resolve().parents[3] / 'docs/examples/judge-calibration-synthetic.json'
    data = json.loads(path.read_text())
    if isinstance(data, dict): data = data['cases']
    assert len(data) >= 10
    assert {row['label_source'] for row in data} == {'synthetic_provisional'}
    result = measure_calibration(data, [])
    assert result['case_count'] > 0
    with pytest.raises(ValueError, match='Human-approved'):
        measure_calibration(data, [], require_human=True)


def test_human_labels_require_reviewer_and_version():
    labels = [case('a', 'pass', label_source='human')]
    with pytest.raises(ValueError, match='reviewer'):
        measure_calibration(labels, [])
    labels[0].update(reviewer='Reviewer A', label_version='v1')
    assert measure_calibration(labels, [], require_human=True)['labels_human_approved'] is True


@pytest.mark.parametrize('problem', ['group_overlap', 'duplicate_case', 'unknown_prediction', 'duplicate_prediction', 'unknown_check', 'invalid_label', 'not_arrays'])
def test_invalid_or_leaking_inputs_are_rejected(problem):
    data = [case('a', 'pass')]
    predictions = [{'case_id': 'a', 'outcomes': {'behavior': 'pass'}}]
    if problem == 'group_overlap': data.append(case('b', 'pass', split='calibration', conversation_group='a'))
    elif problem == 'duplicate_case': data.append(deepcopy(data[0]))
    elif problem == 'unknown_prediction': predictions[0]['case_id'] = 'absent'
    elif problem == 'duplicate_prediction': predictions.append(deepcopy(predictions[0]))
    elif problem == 'unknown_check': predictions[0]['outcomes'] = {'unknown': 'pass'}
    elif problem == 'invalid_label': data[0]['labels']['behavior'] = 'maybe'
    elif problem == 'not_arrays': data = None
    with pytest.raises(ValueError): measure_calibration(data, predictions)
