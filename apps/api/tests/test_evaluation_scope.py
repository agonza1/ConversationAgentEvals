"""Imported evidence can be assessed without running an agent; scores are not receipts."""
from copy import deepcopy

import pytest

from app.services.benchmark_service import get_suite, run_scenario
from app.services.vcon_evidence import build_benchmark_vcon


def sample():
    scenario = get_suite('call-center-voice-ai')['scenarios'][0]
    return {'suite_id': 'call-center-voice-ai', 'scenario_id': scenario['id'],
            'transcript': scenario['sample_transcript']}


def test_perfect_transcript_score_has_explicit_unverified_execution_scope():
    report = run_scenario(sample(), persist_artifacts=False)
    assert report['overall_score'] == 100
    assert report['verdict'] == 'needs_review'
    scope = report['evaluation_scope']
    assert scope['input_source'] == 'transcript'
    assert scope['artifact_presence'] == {'transcript': True, 'action_trace': False, 'final_state': False}
    assert scope['synthetic'] is None
    assert scope['semantic_review_required'] is True
    assert 'not a verified resolution' in scope['score_meaning']
    assert 'unverified' in report['recommendations'][0]
    assert report['task_completion_score'] is None and report['final_state_score'] is None


def test_imported_vcon_historical_perfect_analysis_is_not_execution_evidence():
    payload = sample()
    vcon = build_benchmark_vcon(payload, payload['transcript'])
    vcon['attachments'] = []  # an external transcript-only vCon, not a CAE-run receipt
    vcon['analysis'] = [{'type': 'report', 'body': {'score': 100, 'final_state': {'complete': True},
                                                 'action_trace': [{'status': 'success'}]}}]
    report = run_scenario({'suite_id': payload['suite_id'], 'scenario_id': payload['scenario_id'], 'vcon': vcon}, persist_artifacts=False)
    assert report['evaluation_scope']['input_source'] == 'vcon'
    assert report['evaluation_scope']['synthetic'] is None  # no recognized source marker
    assert report['evaluation_scope']['artifact_presence'] == {'transcript': True, 'action_trace': False, 'final_state': False}
    assert report['overall_score'] == 100 and report['verdict'] == 'needs_review'
    assert not report['action_trace'] and not report['final_state']
    assert report['ietf_vcon_export']['analysis'][-1]['body']['evaluation_scope'] == report['evaluation_scope']
    assert report['vcon_analysis']['body']['evaluation_scope'] == report['evaluation_scope']


@pytest.mark.parametrize('trace,state,expected_trace,expected_state', [
    ([], {}, False, False), ('[]', '{}', False, False),
    ([{'name': 'update_address', 'status': 'requested'}], {}, True, False),
    ([], {'complete': False}, False, True),
    ([{'name': 'update_address', 'status': 'failed'}], {'complete': False}, True, True),
])
def test_scope_measures_presence_not_success(trace, state, expected_trace, expected_state):
    report = run_scenario({**sample(), 'action_trace': trace, 'final_state': state}, persist_artifacts=False)
    assert report['evaluation_scope']['artifact_presence']['action_trace'] is expected_trace
    assert report['evaluation_scope']['artifact_presence']['final_state'] is expected_state
    assert report['evaluation_scope']['trust'] == 'source-reported; not independently authenticated'


def test_synthetic_full_sample_preserves_its_marker_and_does_not_gain_authenticated_trust():
    payload = sample()
    scenario = get_suite(payload['suite_id'])['scenarios'][0]
    payload.update(action_trace=deepcopy(scenario['sample_action_trace']), final_state=deepcopy(scenario['sample_final_state']))
    vcon = build_benchmark_vcon(payload, payload['transcript'], synthetic=True)
    report = run_scenario({'suite_id': payload['suite_id'], 'scenario_id': payload['scenario_id'], 'vcon': vcon}, persist_artifacts=False)
    assert report['evaluation_scope']['synthetic'] is True
    assert report['evaluation_scope']['artifact_presence'] == {'transcript': True, 'action_trace': True, 'final_state': True}
    assert 'not independently authenticated' in report['evaluation_scope']['trust']
    assert report['verdict'] == 'needs_review'
