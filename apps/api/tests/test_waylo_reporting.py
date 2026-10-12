"""Minimized Steven acceptance observations; no provider calls or credentials."""
from copy import deepcopy

from app.services.execution_metrics import build_metrics_and_timeline
from app.services.waylo_reporting import OBSERVER_CLOCK, OBSERVER_METRIC, observed_coverage, present_run, report_profile


def steven_profile():
    return {
        'voice_events': [
            *[{'event_id': f'caller-{i}/observer-latency', 'event_type': 'latency.observation',
                'turn_id': f'caller-{i}', 'source': 'cae_livekit_observer', 'value_ms': value,
                'unit': 'ms', 'clock_origin': OBSERVER_CLOCK} for i, value in enumerate([1910, 3098], 1)],
            *[{'event_id': f'worker-{i}/latency', 'event_type': 'latency.observation',
                'source': 'waylo.agent.turn_metrics', 'value_ms': value, 'unit': 'ms',
                'turn_index': i * 2, 'clock_origin': 'waylo_worker_utc'} for i, value in enumerate([1652, 2964], 1)],
            *[{'event_id': f'worker-{i}/interrupted', 'event_type': 'playback.interruption_observed',
                'source': 'waylo.agent.turn_metrics', 'interrupted': False} for i in range(3)],
            {'event_id': 'caller-1/sent', 'event_type': 'tester.audio.sent', 'reference_text': 'Tell me the warranty.',
             'accepted_samples': 48000, 'intended_samples': 48000},
            {'event_id': 'agent-1/received', 'event_type': 'target.audio.received'},
        ],
        'tool_events': [{'event_type': 'tool_call.result', 'name': 'search_documents', 'arguments_captured': True,
                         'result_captured': True, 'status': 'success', 'turn_index': 4,
                         'duration_ms': 653, 'duration_clock': 'waylo_worker_wall_clock'}],
        'state_snapshots': [{'phase': phase, 'state': {'request_list': []}} for phase in ['before', 'after', 'after', 'final']],
        'context': {'diagnostics': {'speech_boundary': {'status': 'needs_review', 'comparisons': [
            {'reference_text': 'Tell me the warranty.', 'target_asr_final': 'Tell me warranty.',
             'alignment': 'ordered_one_to_one_provisional', 'scoreable': False}]}}, 'test_expectations': {}},
    }


def test_observer_and_worker_latencies_are_separate_and_not_double_counted():
    profile = steven_profile()
    profile['voice_events'].append(deepcopy(profile['voice_events'][0]))
    report = report_profile(profile)
    marks = report['latency_marks']
    assert [mark['latency_ms'] for mark in marks] == [1910, 3098]
    assert all(mark['kind'] == OBSERVER_METRIC and mark['clock_origin'] == OBSERVER_CLOCK for mark in marks)
    summary, timeline = build_metrics_and_timeline(turns=[], latency_marks=marks, verdict='needs_review', score=None)
    assert summary.latency.count == 2 and summary.latency.avg_ms == 2504
    assert all(event.t_ms is None for event in timeline)  # Unknown chronology is not a synthesized wall clock.
    assert [event['value_ms'] for event in report['evidence_diagnostics']['response_latency']['worker_observations']] == [1652, 2964]


def test_native_false_flags_are_observed_but_handling_was_not_tested():
    report = report_profile(steven_profile())['evidence_diagnostics']['interruption']
    assert report['reported_true'] == 0 and report['reported_false'] == 3
    assert report['status'] == 'observed' and report['handling_status'] == 'not_tested'
    assert report['scoreable'] is False
    profile = steven_profile()
    profile['voice_events'] += [{'event_id': 'barge-1', 'event_type': 'tester.barge_in_scheduled'}]
    profile['voice_events'][4]['interrupted'] = True
    report = report_profile(profile)['evidence_diagnostics']['interruption']
    assert report['reported_true'] == 1 and report['reported_false'] == 2
    assert report['controlled_attempts'] == 1 and report['handling_status'] == 'needs_review'
    report = report_profile({'voice_events': []})['evidence_diagnostics']['interruption']
    assert report['status'] == 'unknown'  # No flags is not a zero-success grade.


def test_unknown_latency_clock_units_and_nonfinite_values_are_not_measured():
    profile = steven_profile()
    for event in profile['voice_events'][:2]:
        event['clock_origin'] = 'unknown'
    assert not report_profile(profile)['latency_marks']
    for value in [True, -1, float('nan'), float('inf')]:
        event = {'event_id': 'invalid', 'event_type': 'latency.observation', 'source': 'cae_livekit_observer',
                 'value_ms': value, 'unit': 'ms', 'clock_origin': OBSERVER_CLOCK}
        assert not report_profile({'voice_events': [event]})['latency_marks']


def test_four_c_coverage_reports_present_payloads_and_keeps_business_state_unknown():
    profile = steven_profile()
    coverage = observed_coverage(tools=profile['tool_events'], voice=profile['voice_events'],
        snapshots=profile['state_snapshots'], diagnostics=profile['context']['diagnostics'], media_dialogs=None, expectations={})
    execution = coverage['agent_execution']
    assert {'tool_arguments', 'tool_results', 'turn_associations'} <= set(execution['observed'])
    assert not {'tool_arguments', 'tool_results', 'turn_associations'} & set(execution['missing'])
    assert 'policy_decisions' in execution['missing']
    assert 'target_asr_final_provisional_alignment' in coverage['speech_boundary']['observed']
    completion = coverage['business_outcome']
    assert 'request_list_final_readback' in completion['observed']
    assert 'catalog_lookup_results' in completion['observed']
    assert 'controlled_expected_request_list' in completion['missing']
    assert 'full_backend_state' in completion['missing']
    assert all(area['scoreable'] is False and area['status'] == 'partial' for area in coverage.values())
    empty = observed_coverage(tools=[], voice=[], snapshots=[], diagnostics={}, media_dialogs=None, expectations={})
    assert all(area['status'] == 'unknown' for area in empty.values())


def test_historical_reporting_does_not_rewrite_evidence_or_accepted_grade(monkeypatch):
    profile = steven_profile()
    monkeypatch.setattr('app.services.vcon_evidence.decode_evidence', lambda _: deepcopy(profile))
    run = {'executor_id': 'waylo_livekit', 'conversations': [{'turns': [], 'latency_marks': [],
        'ietf_vcon_export': {'saved': 'untouched'}, 'judge_reviews': [{'status': 'accepted'}],
        'verdict': 'pass', 'score': 100,
        'metrics_summary': {'latency': {'count': 0}, 'verdict': 'pass', 'score': 100, 'call_resolution_success': 100}}]}
    before = deepcopy(run)
    presented = present_run(deepcopy(run))['conversations'][0]
    assert presented['metrics_summary']['latency']['avg_ms'] == 2504
    assert presented['score'] == 100 and presented['metrics_summary']['score'] == 100
    assert presented['ietf_vcon_export'] == before['conversations'][0]['ietf_vcon_export']
    assert presented['judge_reviews'] == before['conversations'][0]['judge_reviews']
    assert run == before


def test_missing_turn_timing_is_order_only_not_fabricated_400ms():
    summary, timeline = build_metrics_and_timeline(turns=[{'speaker': 'Caller', 'text': 'Hi'},
        {'speaker': 'Agent', 'text': 'Hello'}], latency_marks=[], verdict=None, score=None)
    assert summary.latency.count == 0
    assert [(event.t_ms, event.latency_ms) for event in timeline] == [(None, None), (None, None)]
