import app.main  # Initialize catalog extensions in the same order as API startup.

from app.services.benchmark_suite_run_store import _suite_history_summary


def test_project_suite_trend_uses_the_latest_suites_previous_score():
    summary = _suite_history_summary([
        {'suite_id': 'a', 'average_score': 80, 'scenario_count': 2, 'pass_count': 1},
        {'suite_id': 'b', 'average_score': 10, 'scenario_count': 3, 'pass_count': 2},
        {'suite_id': 'a', 'average_score': None},
        {'suite_id': 'a', 'average_score': 90},
    ])
    assert summary['previous_average_score'] == 90
    assert summary['latest_delta'] == -10
    assert summary['latest_trend'] == 'regressed'
    assert summary['total_scenarios'] == 5
    assert summary['total_passes'] == 3


def test_other_suites_and_missing_suite_ids_do_not_establish_a_baseline():
    for latest in [{'suite_id': 'a', 'average_score': 80}, {'average_score': 80}]:
        summary = _suite_history_summary([latest, {'suite_id': 'b', 'average_score': 10}])
        assert summary['previous_average_score'] is None
        assert summary['latest_delta'] is None
        assert summary['latest_trend'] == 'baseline'


def test_empty_history_remains_unscored():
    summary = _suite_history_summary([])
    assert summary['latest_trend'] == 'unscored'
    assert summary['latest_delta'] is None
