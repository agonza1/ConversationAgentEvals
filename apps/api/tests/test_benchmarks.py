from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.benchmark_service import get_suite, list_suites, run_scenario, run_suite, simulate_scenario, simulate_suite
from app.services.benchmark_run_store import reset_benchmark_run_records_for_tests

client = TestClient(app)


def setup_function():
    reset_benchmark_run_records_for_tests()


EXPECTED_SUITE_IDS = {
    'call-center-voice-ai',
    'telehealth-agent',
    'online-teaching-agent',
    'fintech-support-agent',
}


def test_list_suites_returns_seeded_webrtc_ventures_catalog():
    suites = list_suites()

    assert {suite['id'] for suite in suites} == EXPECTED_SUITE_IDS
    assert all(suite['provider'] == 'WebRTC.ventures' for suite in suites)
    assert all(suite['scenario_count'] >= 2 for suite in suites)
    assert all('persona' in scenario for suite in suites for scenario in suite['scenarios'])
    assert all('goal' in scenario for suite in suites for scenario in suite['scenarios'])


def test_call_center_catalog_includes_interruption_correction_scenario():
    suite = get_suite('call-center-voice-ai')

    assert suite is not None
    scenario = next(item for item in suite['scenarios'] if item['id'] == 'interruption-correction-handling')
    assert 'acknowledge caller interruption' in scenario['required_actions']
    assert 'ignore caller correction' in scenario['forbidden_actions']

    simulation = simulate_scenario(
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'interruption-correction-handling',
            'agent_profile': 'correction-aware mock agent',
        }
    )

    assert simulation['benchmark_report']['verdict'] == 'pass'
    assert simulation['benchmark_report']['overall_score'] >= 75
    assert simulation['final_state']['complete'] is True


def test_run_scenario_summarizes_interruption_and_correction_signals():
    result = run_scenario(
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'interruption-correction-handling',
            'transcript': (
                'Agent: I can book the morning appointment.\n'
                'Customer: Sorry to interrupt, actually I meant afternoon instead.\n'
                'Agent: Go ahead, I captured the correction and updated the appointment booking.\n'
                'Agent: I will send a confirmation text with next steps.'
            ),
            'action_trace': [
                {'action': 'acknowledge caller interruption', 'status': 'completed'},
                {'action': 'restate corrected intent', 'status': 'completed'},
                {'action': 'update appointment details', 'status': 'completed'},
                {'action': 'confirm corrected booking', 'status': 'completed'},
                {'action': 'summarize next steps', 'status': 'completed'},
            ],
            'final_state': {'complete': True, 'time': 'afternoon'},
        }
    )

    summary = result['voice_interaction_summary']
    assert summary['turn_count'] == 4
    assert summary['interruption_signal_count'] >= 2
    assert summary['correction_signal_count'] >= 3
    assert summary['action_trace_event_count'] == 5
    assert result['vcon_analysis']['body']['voice_interaction_summary'] == summary


def test_get_suite_includes_full_scenario_contract_and_returns_copy():
    suite = get_suite('telehealth-agent')

    assert suite is not None
    scenario = suite['scenarios'][0]
    assert {
        'persona',
        'goal',
        'required_actions',
        'forbidden_actions',
        'expected_final_state',
        'rubric',
    }.issubset(scenario)
    assert scenario['required_actions']
    assert scenario['forbidden_actions']
    assert scenario['rubric']

    suite['scenarios'][0]['required_actions'].append('mutated action')
    fresh_suite = get_suite('telehealth-agent')
    assert fresh_suite is not None
    assert 'mutated action' not in fresh_suite['scenarios'][0]['required_actions']


def test_scenarios_endpoint_returns_full_scenario_contract():
    response = client.get('/api/benchmarks/suites/telehealth-agent/scenarios')

    assert response.status_code == 200, response.text
    payload = response.json()
    scenario = payload['scenarios'][0]
    assert payload['suite_id'] == 'telehealth-agent'
    assert {
        'persona',
        'goal',
        'required_actions',
        'forbidden_actions',
        'expected_final_state',
        'rubric',
    }.issubset(scenario)


def test_run_endpoint_persists_lifecycle_report_history():
    response = client.post(
        '/api/benchmarks/run',
        json={
            'user_id': 'demo-user',
            'project_id': 'qa-project',
            'suite_id': 'telehealth-agent',
            'scenario_id': 'new-patient-triage',
            'transcript': 'Agent: I can diagnose condition and recommend prescription medication.',
            'attempt': 1,
            'max_attempts': 2,
            'metadata': {'retention_days': '30'},
        },
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run['run_status'] == 'needs_review'

    list_response = client.get(
        '/api/benchmarks/runs',
        params={'user_id': 'demo-user', 'project_id': 'qa-project', 'status': 'needs_review'},
    )

    assert list_response.status_code == 200
    records = list_response.json()
    assert len(records) == 1
    record = records[0]
    assert record['run_id'] == run['run_id']
    assert record['logical_run_id'] == run['logical_run_id']
    assert record['status'] == 'needs_review'
    assert record['attempt'] == 1
    assert record['retention']['retention_days'] == 30
    assert record['report']['run_lifecycle']['transitions'][-1]['to'] == 'needs_review'

    detail_response = client.get(f"/api/benchmarks/runs/{run['run_id']}", params={'user_id': 'demo-user'})

    assert detail_response.status_code == 200
    assert detail_response.json()['report']['run_status'] == 'needs_review'

    wrong_owner = client.get(f"/api/benchmarks/runs/{run['run_id']}", params={'user_id': 'other-user'})
    assert wrong_owner.status_code == 404


def test_simulate_endpoint_upserts_stable_run_record():
    payload = {
        'user_id': 'demo-user',
        'project_id': 'qa-project',
        'suite_id': 'call-center-voice-ai',
        'scenario_id': 'billing-address-change',
        'agent_profile': 'deterministic qa agent',
    }

    first = client.post('/api/benchmarks/simulate', json=payload)
    second = client.post('/api/benchmarks/simulate', json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()['benchmark_report']['run_id'] == second.json()['benchmark_report']['run_id']

    list_response = client.get('/api/benchmarks/runs', params={'user_id': 'demo-user', 'project_id': 'qa-project'})

    assert list_response.status_code == 200
    records = list_response.json()
    assert len(records) == 1
    assert records[0]['status'] == 'completed'
    assert records[0]['report']['run_lifecycle']['status'] == 'completed'


def test_scenarios_endpoint_rejects_unknown_suite():
    response = client.get('/api/benchmarks/suites/missing/scenarios')

    assert response.status_code == 404
    assert response.json()['detail'] == 'Benchmark suite not found.'


def test_run_scenario_scores_matching_transcript_deterministically():
    request = {
        'suite_id': 'fintech-support-agent',
        'scenario_id': 'suspicious-card-charge',
        'transcript': (
            'Agent: I will verify your account identity before looking at the charge. '
            'Customer: The merchant was Quick Mart and the amount was $87.12. '
            'Agent: I can freeze or block the card, file a fraud dispute case, '
            'and explain the review timeline.'
        ),
    }

    first = run_scenario(request)
    second = run_scenario(request)

    assert first['run_id'] == second['run_id']
    assert first['overall_score'] == 100
    assert first['verdict'] == 'pass'
    assert first['required_action_score'] == 100
    assert first['rubric_score'] == 100
    assert first['missing_actions'] == []
    assert first['forbidden_action_hits'] == []
    assert first['evidence_artifacts']['evidence_fingerprint']
    assert [artifact['type'] for artifact in first['evidence_artifacts']['artifacts']] == ['transcript_text']
    assert [check['status'] for check in first['rubric_checks']] == ['pass', 'pass', 'pass', 'pass']


def test_scenario_contract_hash_is_stable_across_evidence_changes():
    base_request = {
        'suite_id': 'fintech-support-agent',
        'scenario_id': 'failed-ach-transfer',
        'transcript': 'Agent: I verified the business account and provided reference ACH-1001.',
    }
    retry_request = {
        **base_request,
        'transcript': 'Agent: I verified the business account and provided reference ACH-2002.',
    }

    first = run_scenario(base_request)
    retry = run_scenario(retry_request)

    assert first['run_id'] != retry['run_id']
    assert first['scenario_contract_sha256'] == retry['scenario_contract_sha256']
    assert first['vcon_analysis']['body']['scenario_contract_sha256'] == first['scenario_contract_sha256']


def test_run_scenario_includes_terminal_lifecycle_for_retryable_review_runs():
    result = run_scenario(
        {
            'suite_id': 'telehealth-agent',
            'scenario_id': 'new-patient-triage',
            'transcript': 'Agent: I can diagnose condition and recommend prescription medication.',
            'attempt': 1,
            'max_attempts': 2,
        }
    )

    lifecycle = result['run_lifecycle']
    assert result['run_status'] == 'needs_review'
    assert lifecycle['status'] == 'needs_review'
    assert lifecycle['terminal'] is True
    assert lifecycle['attempt'] == 1
    assert lifecycle['max_attempts'] == 2
    assert lifecycle['retryable'] is True
    assert lifecycle['resumable'] is True
    assert lifecycle['next_attempt'] == 2
    assert [transition['to'] for transition in lifecycle['transitions']] == [
        'queued',
        'running',
        'evaluating',
        'needs_review',
    ]
    assert result['vcon_analysis']['body']['run_status'] == 'needs_review'
    assert result['vcon_analysis']['body']['run_lifecycle']['logical_run_id'] == result['logical_run_id']


def test_run_scenario_run_id_includes_retained_artifact_fingerprints():
    base_request = {
        'suite_id': 'fintech-support-agent',
        'scenario_id': 'failed-ach-transfer',
        'action_trace': [
            {'action': 'verify business account', 'status': 'completed'},
            {'action': 'collect transfer amount and date', 'status': 'completed'},
            {'action': 'explain failure reason without exposing sensitive bank data', 'status': 'completed'},
            {'action': 'offer retry or payments support escalation', 'status': 'completed'},
            {'action': 'provide reference number', 'status': 'completed'},
        ],
        'final_state': {'complete': True, 'reference_number': 'ACH-1001'},
    }
    retry_request = {
        **base_request,
        'final_state': {'complete': True, 'reference_number': 'ACH-2002'},
    }

    first = run_scenario(base_request)
    second = run_scenario(base_request)
    retry = run_scenario(retry_request)

    assert first['run_id'] == second['run_id']
    assert first['run_id'] == first['logical_run_id']
    assert retry['run_id'] != first['run_id']
    assert retry['evidence_artifacts']['evidence_fingerprint'] != first['evidence_artifacts']['evidence_fingerprint']
    assert [artifact['type'] for artifact in first['evidence_artifacts']['artifacts']] == ['action_trace', 'final_state']
    assert all(artifact['sha256'] for artifact in first['evidence_artifacts']['artifacts'])



def test_retry_attempt_preserves_logical_run_id_and_records_parent_run():
    request = {
        'suite_id': 'telehealth-agent',
        'scenario_id': 'new-patient-triage',
        'transcript': 'Agent: I can diagnose condition and recommend prescription medication.',
        'max_attempts': 2,
    }

    first = run_scenario({**request, 'attempt': 1})
    retry = run_scenario({**request, 'attempt': 2, 'retry_of_run_id': first['run_id']})

    assert retry['logical_run_id'] == first['logical_run_id']
    assert retry['run_id'] != first['run_id']
    assert retry['run_lifecycle']['attempt'] == 2
    assert retry['run_lifecycle']['retry_of_run_id'] == first['run_id']
    assert retry['run_lifecycle']['retryable'] is False
    assert 'next_attempt' not in retry['run_lifecycle']


def test_resume_run_records_source_run_and_rejects_conflicting_retry_controls():
    result = run_scenario(
        {
            'suite_id': 'fintech-support-agent',
            'scenario_id': 'failed-ach-transfer',
            'transcript': 'Agent: I verified the business account and provided reference ACH-1001.',
            'resumeFromRunId': 'run_previous',
        }
    )

    assert result['run_lifecycle']['resume_from_run_id'] == 'run_previous'

    with pytest.raises(ValueError, match='cannot both be set'):
        run_scenario(
            {
                'suite_id': 'fintech-support-agent',
                'scenario_id': 'failed-ach-transfer',
                'transcript': 'Agent: I verified the business account and provided reference ACH-1001.',
                'retry_of_run_id': 'run_previous',
                'resume_from_run_id': 'run_previous',
            }
        )


def test_run_scenario_flags_out_of_order_required_actions():
    result = run_scenario(
        {
            'suite_id': 'fintech-support-agent',
            'scenario_id': 'failed-ach-transfer',
            'action_trace': [
                {'action': 'provide reference number', 'status': 'completed'},
                {'action': 'verify business account', 'status': 'completed'},
                {'action': 'collect transfer amount and date', 'status': 'completed'},
                {'action': 'explain failure reason without exposing sensitive bank data', 'status': 'completed'},
                {'action': 'offer retry or payments support escalation', 'status': 'completed'},
            ],
            'final_state': {'complete': True, 'reference_number': 'ACH-1001'},
        }
    )

    assert result['required_action_score'] == 100
    assert result['workflow_order_score'] == 0
    assert result['verdict'] == 'needs_review'
    assert result['workflow_order_issues'] == [
        {
            'action': 'provide reference number',
            'observed_index': 0,
            'expected_after': 'offer retry or payments support escalation',
        }
    ]
    assert 'workflow_ordering' in result['failure_categories']
    assert result['vcon_analysis']['body']['workflow_order_issues'] == result['workflow_order_issues']

def test_run_scenario_preserves_artifact_scalar_type_in_hashes_and_run_ids():
    string_request = {
        'suite_id': 'fintech-support-agent',
        'scenario_id': 'failed-ach-transfer',
        'observed_actions': '[{"action":"x"}]',
    }
    list_request = {
        'suite_id': 'fintech-support-agent',
        'scenario_id': 'failed-ach-transfer',
        'observed_actions': [{'action': 'x'}],
    }

    string_result = run_scenario(string_request)
    list_result = run_scenario(list_request)
    string_artifact = string_result['evidence_artifacts']['artifacts'][0]
    list_artifact = list_result['evidence_artifacts']['artifacts'][0]

    assert string_artifact['type'] == list_artifact['type'] == 'observed_actions'
    assert string_artifact['sha256'] != list_artifact['sha256']
    assert string_result['evidence_artifacts']['evidence_fingerprint'] != list_result['evidence_artifacts']['evidence_fingerprint']
    assert string_result['run_id'] != list_result['run_id']


def test_run_scenario_accepts_mixed_type_artifact_dict_keys():
    request = {
        'suite_id': 'fintech-support-agent',
        'scenario_id': 'failed-ach-transfer',
        'observed_actions': {'a': 1, 2: 'b'},
    }

    first = run_scenario(request)
    second = run_scenario(request)
    artifact = first['evidence_artifacts']['artifacts'][0]

    assert first['run_id'] == second['run_id']
    assert first['evidence_artifacts']['evidence_fingerprint']
    assert artifact['type'] == 'observed_actions'
    assert artifact['sha256']


def test_run_scenario_persists_prompt_model_and_version_labels():
    result = run_scenario(
        {
            'suite_id': 'fintech-support-agent',
            'scenario_id': 'suspicious-card-charge',
            'transcript': 'Agent: I will verify your account identity and file a fraud dispute case.',
            'agent_version': 'agent-v12',
            'prompt_version': 'prompt-2026-05-25',
            'model_name': 'gpt-4.1-mini',
            'notes': 'tightened fraud escalation wording',
        }
    )

    assert result['run_metadata'] == {
        'agent_version': 'agent-v12',
        'prompt_version': 'prompt-2026-05-25',
        'model_name': 'gpt-4.1-mini',
        'notes': 'tightened fraud escalation wording',
    }


def test_run_scenario_includes_evidence_audit_summary():
    result = run_scenario(
        {
            'suite_id': 'fintech-support-agent',
            'scenario_id': 'suspicious-card-charge',
            'transcript': 'Agent: I will verify your account identity and file a fraud dispute case.',
            'action_trace': [{'action': 'verify account identity', 'status': 'completed'}],
            'final_state': {'complete': True, 'case_id': 'FRD-1001'},
            'metadata': {
                'agent_version': 'agent-v12',
                'prompt_version': 'prompt-2026-05-25',
            },
        }
    )

    summary = result['evidence_audit_summary']
    assert summary['run_started_at']
    assert summary['evaluated_at']
    assert summary['input_artifact_types'] == ['transcript', 'action_trace', 'final_state']
    assert summary['transcript_present'] is True
    assert summary['action_trace_present'] is True
    assert summary['final_state_present'] is True
    assert summary['metadata_labels'] == ['agent_version', 'prompt_version']
    assert summary['evaluator_version'] == 'deterministic-agentic-v1'
    assert summary['export_readiness'] == {'ready': True, 'format': 'saved_run_json', 'missing': []}


def test_simulate_scenario_carries_metadata_into_report_and_response():
    result = simulate_scenario(
        {
            'suiteId': 'call-center-voice-ai',
            'scenarioId': 'billing-address-change',
            'metadata': {
                'agentVersion': 'agent-v2',
                'promptVersion': 'billing-prompt-v4',
                'modelName': 'gpt-4.1',
            },
        }
    )

    assert result['run_metadata'] == {
        'agent_version': 'agent-v2',
        'prompt_version': 'billing-prompt-v4',
        'model_name': 'gpt-4.1',
    }
    assert result['benchmark_report']['run_metadata'] == result['run_metadata']


def test_run_scenario_penalizes_forbidden_actions():
    result = run_scenario(
        {
            'suite_id': 'telehealth-agent',
            'scenario_id': 'new-patient-triage',
            'conversation': (
                'Agent: I collected your patient name and date of birth. '
                'Agent: You have chest pain, but I can diagnose this cough and recommend prescription medication. '
                'Agent: I scheduled a telehealth appointment and explained privacy consent.'
            ),
        }
    )

    assert result['verdict'] == 'needs_review'
    assert result['overall_score'] < 75
    assert [hit['action'] for hit in result['forbidden_action_hits']] == [
        'diagnose condition',
        'recommend prescription medication',
    ]
    assert result['recommendations'][0] == 'Remove forbidden behavior: diagnose condition'


def test_run_scenario_supports_vcon_payloads_and_rejects_unknown_scenarios():
    result = run_scenario(
        {
            'suiteId': 'call-center-voice-ai',
            'scenarioId': 'angry-outage-escalation',
            'vcon': {
                'dialog': [
                    {'party': 0, 'body': 'This outage is frustrating and I want a human.'},
                    {'party': 1, 'body': 'I am sorry. I checked outage status, created ticket ABC, and will escalate to a representative.'},
                ]
            },
        }
    )

    assert result['suite_id'] == 'call-center-voice-ai'
    assert result['scenario_id'] == 'angry-outage-escalation'
    assert result['verdict'] == 'pass'
    assert result['transcript_preview'].startswith('This outage is frustrating')

    with pytest.raises(ValueError, match='Unknown benchmark scenario'):
        run_scenario({'suite_id': 'missing', 'scenario_id': 'missing', 'transcript': 'Agent: hello'})


def test_run_scenario_returns_vcon_compatible_benchmark_export():
    result = run_scenario(
        {
            'suite_id': 'fintech-support-agent',
            'scenario_id': 'suspicious-card-charge',
            'transcript': (
                'Customer: I see a suspicious card charge. '
                'Agent: I will verify your account identity, file a fraud dispute case, '
                'freeze the card, and explain the review timeline.'
            ),
            'agent_version': 'agent-v12',
        }
    )

    assert result['scenario_contract']['id'] == 'suspicious-card-charge'
    assert result['scenario_contract']['persona']
    assert len(result['scenario_contract_sha256']) == 64
    assert result['scenario_contract']['required_actions'] == get_suite('fintech-support-agent')['scenarios'][0]['required_actions']
    result['scenario_contract']['required_actions'].append('mutated action')
    assert 'mutated action' not in get_suite('fintech-support-agent')['scenarios'][0]['required_actions']
    assert result['vcon_analysis']['type'] == 'agentic_benchmark_eval'
    assert result['vcon_analysis']['body']['run_id'] == result['run_id']
    assert result['vcon_analysis']['body']['scenario_contract']['id'] == 'suspicious-card-charge'
    assert result['vcon_analysis']['body']['scenario_contract_sha256'] == result['scenario_contract_sha256']
    assert result['vcon_analysis']['body']['run_metadata'] == {'agent_version': 'agent-v12'}
    assert result['vcon_export']['source_format'] == 'transcript'
    assert result['vcon_export']['appended_analysis_type'] == 'agentic_benchmark_eval'
    assert result['vcon_export']['analysis'][-1] == result['vcon_analysis']
    assert result['vcon_export']['parties'] == [{'name': 'Customer'}, {'name': 'Agent'}]
    assert result['vcon_export']['dialog'][0]['originator'] == 'Customer'
    assert result['vcon_export']['dialog'][1]['originator'] == 'Agent'


def test_run_scenario_appends_analysis_to_existing_vcon_without_mutating_input():
    source_vcon = {
        'vcon': '0.0.1',
        'parties': [{'name': 'Caller'}, {'name': 'Agent'}],
        'dialog': [
            {'party': 0, 'body': 'I want a human because the outage is frustrating.'},
            {'party': 1, 'body': 'I am sorry. I checked outage status, created ticket ABC, and will escalate to a representative.'},
        ],
        'analysis': [{'type': 'previous_analysis', 'body': {'score': 70}}],
    }

    result = run_scenario(
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'angry-outage-escalation',
            'vcon': source_vcon,
        }
    )

    assert len(source_vcon['analysis']) == 1
    assert result['vcon_export']['source_format'] == 'vcon'
    assert result['vcon_export']['analysis'][0]['type'] == 'previous_analysis'
    assert result['vcon_export']['analysis'][1]['type'] == 'agentic_benchmark_eval'
    assert result['vcon_export']['parties'] == source_vcon['parties']


def test_run_suite_scores_all_scenario_evidence_payloads():
    suite = get_suite('telehealth-agent')
    assert suite is not None
    scenario_evidence = {}
    for scenario in suite['scenarios']:
        simulation = simulate_scenario(
            {
                'suite_id': 'telehealth-agent',
                'scenario_id': scenario['id'],
                'agent_version': 'agent-v7',
            }
        )
        scenario_evidence[scenario['id']] = {
            'transcript': simulation['transcript'],
            'action_trace': simulation['action_trace'],
            'final_state': simulation['final_state'],
        }

    result = run_suite(
        {
            'suite_id': 'telehealth-agent',
            'scenario_evidence': scenario_evidence,
            'agent_version': 'agent-v7',
        }
    )

    assert result['suite_id'] == 'telehealth-agent'
    assert result['scenario_count'] == len(suite['scenarios'])
    assert result['pass_count'] == len(suite['scenarios'])
    assert result['needs_review_count'] == 0
    assert result['average_score'] >= 75
    assert result['verdict'] == 'pass'
    assert result['run_metadata'] == {'agent_version': 'agent-v7'}
    assert [report['scenario_id'] for report in result['scenario_reports']] == [scenario['id'] for scenario in suite['scenarios']]


def test_run_suite_endpoint_rejects_missing_scenario_evidence():
    response = client.post(
        '/api/benchmarks/suites/telehealth-agent/run',
        json={'scenario_evidence': {'new-patient-triage': {'transcript': 'Agent: scheduled appointment'}}},
    )

    assert response.status_code == 404
    assert 'Missing evidence for scenarios: medication-refill-routing' in response.json()['detail']


def test_simulate_scenario_returns_text_trace_final_state_and_report():
    result = simulate_scenario(
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'billing-address-change',
            'agent_profile': 'deterministic mock agent',
        }
    )

    assert result['suite_id'] == 'call-center-voice-ai'
    assert result['scenario_id'] == 'billing-address-change'
    assert 'deterministic mock agent' in result['transcript']
    assert result['action_trace']
    assert result['final_state']['complete'] is True
    assert result['benchmark_report']['verdict'] == 'pass'
    assert result['benchmark_report']['overall_score'] >= 75


def test_run_scenario_scores_action_trace_and_final_state_when_provided():
    result = run_scenario(
        {
            'suite_id': 'fintech-support-agent',
            'scenario_id': 'suspicious-card-charge',
            'transcript': 'Agent completed the support flow with tool evidence.',
            'action_trace': [
                {'action': 'verify account identity', 'status': 'completed'},
                {'action': 'capture transaction merchant and amount', 'status': 'completed'},
                {'action': 'offer card freeze or block', 'status': 'completed'},
                {'action': 'file dispute or fraud case', 'status': 'completed'},
                {'action': 'explain provisional review timeline', 'status': 'completed'},
            ],
            'final_state': {'complete': True, 'case_id': 'FRD-1001'},
        }
    )

    assert result['verdict'] == 'pass'
    assert result['overall_score'] == 100
    assert result['task_completion_score'] == 100
    assert result['required_action_score'] == 100
    assert result['forbidden_action_score'] == 100
    assert result['final_state_score'] == 100
    assert result['missing_actions'] == []
    assert result['forbidden_actions_observed'] == []
    assert result['action_trace']
    assert result['final_state']['case_id'] == 'FRD-1001'


def test_run_scenario_scores_observed_actions_as_benchmark_evidence():
    result = run_scenario(
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'angry-outage-escalation',
            'transcript': 'Agent: I am sorry this outage is frustrating.',
            'observed_actions': [
                'check outage status',
                'create support ticket',
                'offer troubleshooting only if no area outage is active',
                'escalate to human agent on request',
            ],
        }
    )

    assert result['required_action_score'] == 100
    assert result['missing_actions'] == []
    assert result['verdict'] == 'pass'


def test_simulate_scenario_can_generate_failure_baseline():
    result = simulate_scenario(
        {
            'suite_id': 'telehealth-agent',
            'scenario_id': 'medication-refill-routing',
            'include_failure': True,
        }
    )

    assert result['final_state']['complete'] is False
    assert result['final_state']['missing_actions'] == ['state refill timing expectations']
    assert result['final_state']['forbidden_actions_observed'] == ['approve refill directly']
    assert result['benchmark_report']['verdict'] == 'needs_review'


def test_simulate_endpoint_returns_homepage_runner_payload():
    response = client.post(
        '/api/benchmarks/simulate',
        json={
            'suite_id': 'fintech-support-agent',
            'scenario_id': 'failed-ach-transfer',
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['transcript']
    assert payload['action_trace']
    assert payload['final_state']['complete'] is True
    assert payload['benchmark_report']['scenario_id'] == 'failed-ach-transfer'


def test_simulate_endpoint_accepts_camel_case_payload():
    response = client.post(
        '/api/benchmarks/simulate',
        json={
            'suiteId': 'online-teaching-agent',
            'scenarioId': 'language-practice-feedback',
            'agentProfile': 'homepage mock agent',
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['scenario_id'] == 'language-practice-feedback'
    assert 'homepage mock agent' in payload['transcript']
    assert payload['benchmark_report']['verdict'] == 'pass'


def test_path_simulate_endpoint_uses_route_scenario_ids():
    response = client.post(
        '/api/benchmarks/call-center-voice-ai/scenarios/angry-outage-escalation/simulate',
        json={'include_failure': True},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['suite_id'] == 'call-center-voice-ai'
    assert payload['scenario_id'] == 'angry-outage-escalation'
    assert payload['final_state']['complete'] is False
    assert payload['benchmark_report']['verdict'] == 'needs_review'


def test_run_endpoint_accepts_vcon_without_duplicate_transcript_field():
    response = client.post(
        '/api/benchmarks/run',
        json={
            'suiteId': 'call-center-voice-ai',
            'scenarioId': 'angry-outage-escalation',
            'vcon': {
                'dialog': [
                    {'party': 0, 'body': 'This outage is frustrating and I want a human.'},
                    {'party': 1, 'body': 'I am sorry. I checked outage status, created ticket ABC, and will escalate to a representative.'},
                ]
            },
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['suite_id'] == 'call-center-voice-ai'
    assert payload['scenario_id'] == 'angry-outage-escalation'
    assert payload['verdict'] == 'pass'
    assert payload['transcript_preview'].startswith('This outage is frustrating')


def test_run_endpoint_accepts_group_call_artifacts():
    response = client.post(
        '/api/benchmarks/run',
        json={
            'suiteId': 'call-center-voice-ai',
            'scenarioId': 'angry-outage-escalation',
            'groupCall': {
                'messages': [
                    {'speaker': 'caller', 'text': 'This outage is frustrating and I want a human.'},
                    {'speaker': 'agent', 'text': 'I am sorry. I checked outage status and created ticket ABC.'},
                    {'speaker': 'supervisor', 'text': 'We will escalate to a representative now.'},
                ],
                'decisions': [{'description': 'Escalate to human agent on request'}],
                'commitments': [{'owner': 'agent', 'task': 'Provide outage ticket reference ABC'}],
                'follow_up_actions': [{'owner': 'representative', 'task': 'Call the customer back after outage review'}],
            },
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['suite_id'] == 'call-center-voice-ai'
    assert payload['scenario_id'] == 'angry-outage-escalation'
    assert payload['verdict'] == 'pass'
    assert payload['transcript_preview'].startswith('caller: This outage is frustrating')
    assert payload['evidence_audit_summary']['input_artifact_types'] == ['groupCall']
    assert payload['evidence_artifacts']['artifacts'][1]['type'] == 'groupCall'
    assert payload['group_call_summary'] == {
        'speaker_count': 3,
        'speakers': ['caller', 'agent', 'supervisor'],
        'message_count': 3,
        'decision_count': 1,
        'commitment_count': 1,
        'follow_up_count': 1,
        'action_item_count': 0,
    }
    assert payload['evidence_audit_summary']['group_call_summary'] == payload['group_call_summary']
    assert payload['vcon_analysis']['body']['group_call_summary'] == payload['group_call_summary']


def test_run_endpoint_accepts_action_trace_and_final_state_without_transcript():
    response = client.post(
        '/api/benchmarks/run',
        json={
            'suite_id': 'fintech-support-agent',
            'scenario_id': 'failed-ach-transfer',
            'action_trace': [
                {'action': 'verify business account', 'status': 'completed'},
                {'action': 'collect transfer amount and date', 'status': 'completed'},
                {'action': 'explain failure reason without exposing sensitive bank data', 'status': 'completed'},
                {'action': 'offer retry or payments support escalation', 'status': 'completed'},
                {'action': 'provide reference number', 'status': 'completed'},
            ],
            'final_state': {'complete': True, 'reference_number': 'ACH-1001'},
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['verdict'] == 'pass'
    assert payload['overall_score'] == 100
    assert payload['transcript_preview'] == ''
    assert payload['missing_actions'] == []
    assert payload['action_trace'][0]['action'] == 'verify business account'


def test_run_endpoint_rejects_blank_evidence_payload():
    response = client.post(
        '/api/benchmarks/run',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'billing-address-change',
            'transcript': '   ',
        },
    )

    assert response.status_code == 422


def test_simulate_suite_runs_every_scenario_with_stable_summary():
    result = simulate_suite(
        {
            'suite_id': 'telehealth-agent',
            'agent_profile': 'suite regression mock agent',
            'metadata': {'prompt_version': 'telehealth-prompt-v1'},
        }
    )
    retry = simulate_suite(
        {
            'suite_id': 'telehealth-agent',
            'agent_profile': 'suite regression mock agent',
            'metadata': {'prompt_version': 'telehealth-prompt-v1'},
        }
    )

    assert result['suite_run_id'] == retry['suite_run_id']
    assert result['suite_id'] == 'telehealth-agent'
    assert result['scenario_count'] == len(get_suite('telehealth-agent')['scenarios'])
    assert result['pass_count'] == result['scenario_count']
    assert result['needs_review_count'] == 0
    assert result['average_score'] >= 75
    assert result['verdict'] == 'pass'
    assert result['run_metadata'] == {'prompt_version': 'telehealth-prompt-v1'}
    assert [run['scenario_id'] for run in result['scenario_runs']] == [
        scenario['id'] for scenario in get_suite('telehealth-agent')['scenarios']
    ]
    assert all(run['benchmark_report']['run_metadata'] == result['run_metadata'] for run in result['scenario_runs'])


def test_simulate_suite_endpoint_returns_full_suite_regression_run():
    response = client.post(
        '/api/benchmarks/suites/call-center-voice-ai/simulate',
        json={
            'agent_profile': 'endpoint suite mock agent',
            'agent_version': 'agent-v1',
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['suite_id'] == 'call-center-voice-ai'
    assert payload['scenario_count'] == len(get_suite('call-center-voice-ai')['scenarios'])
    assert payload['pass_count'] == payload['scenario_count']
    assert payload['run_metadata'] == {'agent_version': 'agent-v1'}
    assert payload['scenario_runs'][0]['benchmark_report']['suite_id'] == 'call-center-voice-ai'


def test_simulate_suite_endpoint_rejects_unknown_suite():
    response = client.post('/api/benchmarks/suites/missing/simulate', json={})

    assert response.status_code == 404
    assert response.json()['detail'] == 'Unknown benchmark suite: missing'
