from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.benchmark_service import get_suite, list_suites, run_scenario, simulate_scenario

client = TestClient(app)


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
    assert [check['status'] for check in first['rubric_checks']] == ['pass', 'pass', 'pass', 'pass']


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
