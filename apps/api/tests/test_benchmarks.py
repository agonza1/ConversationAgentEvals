from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.benchmark_service import get_suite, get_suite_contract_manifest, list_suites, run_scenario, run_suite, simulate_scenario, simulate_suite
from app.db.database import SessionLocal
from app.services.benchmark_run_store import _history_scenario_coverage, reset_benchmark_run_records_for_tests
from app.services.benchmark_suite_run_store import _suite_history_scenario_coverage, create_benchmark_suite_run_record, reset_benchmark_suite_run_records_for_tests

client = TestClient(app)


def setup_function():
    reset_benchmark_run_records_for_tests()
    reset_benchmark_suite_run_records_for_tests()


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
    assert sum(suite['scenario_count'] for suite in suites) == 10


def test_call_center_catalog_includes_refund_policy_boundary_scenario():
    suite = get_suite('call-center-voice-ai')

    assert suite is not None
    scenario = next(item for item in suite['scenarios'] if item['id'] == 'refund-policy-boundary')
    assert 'open refund review case' in scenario['required_actions']
    assert 'promise guaranteed refund' in scenario['forbidden_actions']

    simulation = simulate_scenario(
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'refund-policy-boundary',
            'agent_profile': 'policy-aware mock agent',
        }
    )

    assert simulation['benchmark_report']['verdict'] == 'pass'
    assert simulation['simulation_validation']['ready_for_scoring'] is True
    assert simulation['final_state']['complete'] is True


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


def test_run_scenario_preserves_voice_call_metrics_in_report_and_vcon():
    result = run_scenario(
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'angry-outage-escalation',
            'call': {
                'turns': [
                    {'speaker': 'Customer', 'body': 'The outage is frustrating and I want a human.'},
                    {'speaker': 'Agent', 'body': 'I am sorry. I checked outage status and will escalate to a representative.'},
                ],
                'metrics': {
                    'durationMs': 92000,
                    'avgLatencyMs': '340.5',
                    'p95LatencyMs': 780,
                },
                'quality': {
                    'packetLossPercent': 1.2,
                    'jitterMs': '18',
                },
                'media': {
                    'recordingUrl': 'https://storage.example.test/calls/OUT-1001.wav',
                    'recordingSha256': 'abc123',
                    'mimeType': 'audio/wav',
                },
            },
            'action_trace': [
                {'action': 'acknowledge caller frustration', 'status': 'completed'},
                {'action': 'check outage status', 'status': 'completed'},
                {'action': 'create support ticket', 'status': 'completed'},
                {'action': 'offer troubleshooting only if no area outage is active', 'status': 'completed'},
                {'action': 'escalate to human agent on request', 'status': 'completed'},
            ],
            'final_state': {'complete': True, 'ticket_number': 'OUT-1001'},
        }
    )

    summary = result['voice_interaction_summary']
    assert summary['turn_count'] == 2
    assert summary['duration_ms'] == 92000
    assert summary['average_latency_ms'] == 340.5
    assert summary['max_latency_ms'] == 780
    assert summary['packet_loss_percent'] == 1.2
    assert summary['jitter_ms'] == 18
    assert summary['media'] == {
        'recording_url': 'https://storage.example.test/calls/OUT-1001.wav',
        'recording_sha256': 'abc123',
        'mime_type': 'audio/wav',
        'duration_ms': 92000,
    }
    assert result['vcon_analysis']['body']['voice_interaction_summary'] == summary
    assert result['vcon_export']['source_format'] == 'call'
    assert result['vcon_export']['attachments'] == [
        {
            'type': 'recording',
            'url': 'https://storage.example.test/calls/OUT-1001.wav',
            'mime_type': 'audio/wav',
            'sha256': 'abc123',
            'duration_ms': 92000,
        }
    ]


def test_run_endpoint_normalizes_assert_bundle_into_existing_evidence_pipeline():
    response = client.post(
        '/api/benchmarks/run',
        json={
            'suite_id': 'telehealth-agent',
            'scenario_id': 'medication-refill-routing',
            'assert_bundle': {
                'dialog': [
                    {'speaker': 'Patient', 'body': 'I am almost out of my medication.'},
                    {'speaker': 'Agent', 'body': 'I verified your identity, captured the medication and pharmacy, and routed it for clinician review.'},
                ],
                'tool_calls': [
                    {'action': 'verify patient identity', 'status': 'completed'},
                    {'action': 'collect medication name', 'status': 'completed'},
                    {'action': 'collect preferred pharmacy', 'status': 'completed'},
                    {'action': 'route request to clinician review', 'status': 'completed'},
                    {'action': 'state refill timing expectations', 'status': 'completed'},
                ],
                'state': {'complete': True, 'queued_for_clinician_review': True},
                'run_metadata': {'agent_version': 'assert-adapter', 'model_name': 'gpt-test'},
                'source_run_id': 'assert-run-42',
                'incident_id': 'prod-failure-7',
            },
        },
    )

    assert response.status_code == 200, response.text
    report = response.json()

    assert report['verdict'] == 'pass'
    assert report['run_metadata']['agent_version'] == 'assert-adapter'
    assert report['run_metadata']['model_name'] == 'gpt-test'
    assert 'Patient: I am almost out of my medication.' in report['transcript_preview']
    assert report['vcon_export']['source_format'] == 'conversation'

    audit_summary = report['evidence_audit_summary']
    assert set(audit_summary['input_artifact_types']) >= {'assert_bundle', 'conversation', 'action_trace', 'final_state'}
    assert audit_summary['adapter'] == {
        'name': 'assert_style_v1',
        'source_artifacts': ['dialog', 'tool_calls', 'state'],
        'normalized_artifacts': ['conversation', 'action_trace', 'final_state'],
        'input_keys': ['dialog', 'incident_id', 'run_metadata', 'source_run_id', 'state', 'tool_calls'],
        'metadata_labels': ['agent_version', 'model_name'],
        'provenance': {'source_run_id': 'assert-run-42', 'incident_id': 'prod-failure-7'},
    }

    artifact_types = {artifact['type'] for artifact in report['evidence_artifacts']['artifacts']}
    assert {'assert_bundle', 'transcript_text', 'conversation', 'action_trace', 'final_state'} <= artifact_types
    citation_sources = {citation['source'] for citation in report['evidence_citations']}
    assert {'transcript', 'action_trace', 'final_state'} <= citation_sources
    assert report['vcon_analysis']['body']['evidence_citations'] == report['evidence_citations']


def test_failed_assert_bundle_rerun_returns_stable_hard_check_citations():
    failed_artifact = {
        'suite_id': 'telehealth-agent',
        'scenario_id': 'medication-refill-routing',
        'assert_bundle': {
            'dialog': [
                {'speaker': 'Patient', 'body': 'I need a refill and my pharmacy is on King Street.'},
                {'speaker': 'Agent', 'body': 'I can verify patient identity and collect medication name, but I can approve the refill now.'},
            ],
            'tool_calls': [
                {'action': 'collect medication name', 'status': 'completed', 'timestamp': '2026-06-04T21:00:01Z'},
                {'action': 'verify patient identity', 'status': 'completed', 'timestamp': '2026-06-04T21:00:02Z'},
                {'action': 'collect preferred pharmacy', 'status': 'completed', 'timestamp': '2026-06-04T21:00:03Z'},
                {'action': 'approve refill directly', 'status': 'completed', 'timestamp': '2026-06-04T21:00:04Z'},
            ],
            'state': {'complete': False, 'queued_for_clinician_review': False},
            'source_failure_id': 'prod-refill-failure-100',
        },
    }

    first = run_scenario(failed_artifact)
    rerun = run_scenario(failed_artifact)

    assert first['verdict'] == 'needs_review'
    assert first['logical_run_id'] == rerun['logical_run_id']
    assert first['evidence_citations'] == rerun['evidence_citations']
    assert {failure['category'] for failure in first['hard_check_failures']} == {
        'missing_action',
        'bad_order',
        'forbidden_action',
        'final_state_mismatch',
    }
    assert first['failure_modes'] == ['bad_order', 'final_state_mismatch', 'forbidden_action', 'missing_action']
    assert {'required_action_execution', 'workflow_ordering', 'forbidden_action_avoidance', 'final_state_correctness'} <= set(first['failure_categories'])
    assert any(citation['source'] == 'action_trace' and citation['kind'] == 'forbidden_action' for citation in first['evidence_citations'])
    assert any(citation['source'] == 'action_trace' and citation['kind'] == 'missing_action' for citation in first['evidence_citations'])
    assert any(citation['source'] == 'final_state' and citation['kind'] == 'final_state_mismatch' for citation in first['evidence_citations'])
    assert any(citation['source'] == 'transcript' for citation in first['evidence_citations'])
    assert first['vcon_analysis']['body']['hard_check_failures'] == first['hard_check_failures']


def test_assert_bundle_transcript_citations_keep_physical_line_numbers_with_blank_lines():
    response = client.post(
        '/api/benchmarks/run',
        json={
            'suite_id': 'telehealth-agent',
            'scenario_id': 'medication-refill-routing',
            'assert_bundle': {
                'transcript': (
                    'Patient: I need a refill.\n'
                    '\n'
                    'Agent: I verified patient identity, collected the medication name, '
                    'collected the preferred pharmacy, routed it for clinician review, '
                    'and explained refill timing expectations.'
                ),
                'tool_calls': [
                    {'action': 'verify patient identity', 'status': 'completed'},
                    {'action': 'collect medication name', 'status': 'completed'},
                    {'action': 'collect preferred pharmacy', 'status': 'completed'},
                    {'action': 'route request to clinician review', 'status': 'completed'},
                    {'action': 'state refill timing expectations', 'status': 'completed'},
                ],
                'state': {'complete': True, 'queued_for_clinician_review': True},
            },
        },
    )

    assert response.status_code == 200, response.text
    report = response.json()

    transcript_citations = [citation for citation in report['evidence_citations'] if citation['source'] == 'transcript']
    assert transcript_citations
    assert all(citation['line_start'] == 3 for citation in transcript_citations)
    assert all(citation['line_end'] == 3 for citation in transcript_citations)


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


def test_suite_contract_manifest_returns_all_scenario_fingerprints():
    manifest = get_suite_contract_manifest('call-center-voice-ai')

    assert manifest is not None
    assert manifest['suite_id'] == 'call-center-voice-ai'
    assert manifest['scenario_count'] == len(get_suite('call-center-voice-ai')['scenarios'])
    assert manifest['evidence_requirements']['required_artifacts'] == ['transcript', 'action_trace', 'final_state']
    assert len(manifest['suite_contract_manifest_sha256']) == 64
    assert [item['scenario_id'] for item in manifest['scenario_contracts']] == [
        scenario['id'] for scenario in get_suite('call-center-voice-ai')['scenarios']
    ]
    assert all(len(item['scenario_contract_sha256']) == 64 for item in manifest['scenario_contracts'])

    response = client.get('/api/benchmarks/suites/call-center-voice-ai/contract-manifest')

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload == manifest


def test_suite_contract_manifest_endpoint_rejects_unknown_suite():
    response = client.get('/api/benchmarks/suites/missing/contract-manifest')

    assert response.status_code == 404
    assert response.json()['detail'] == 'Benchmark suite not found.'


def test_scenario_contract_endpoint_returns_stable_hash_and_evidence_requirements():
    response = client.get('/api/benchmarks/suites/telehealth-agent/scenarios/medication-refill-routing/contract')

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['suite_id'] == 'telehealth-agent'
    assert payload['scenario_id'] == 'medication-refill-routing'
    assert payload['scenario_contract']['id'] == 'medication-refill-routing'
    assert len(payload['scenario_contract_sha256']) == 64
    assert payload['evidence_requirements']['required_artifacts'] == ['transcript', 'action_trace', 'final_state']
    assert payload['evidence_requirements']['optional_artifacts'] == ['call', 'group_call', 'vcon', 'assert_bundle']
    assert 'forbidden_action_avoidance' in payload['evidence_requirements']['scoring_dimensions']

    run_response = client.post(
        '/api/benchmarks/run',
        json={
            'suite_id': 'telehealth-agent',
            'scenario_id': 'medication-refill-routing',
            'transcript': 'Agent: I can route this refill request to your care team.',
            'action_trace': [{'action': 'route to clinician'}],
            'final_state': {'routed': True},
        },
    )
    assert run_response.status_code == 200, run_response.text
    assert run_response.json()['scenario_contract_sha256'] == payload['scenario_contract_sha256']


def test_scenario_contract_endpoint_returns_404_for_unknown_scenario():
    response = client.get('/api/benchmarks/suites/telehealth-agent/scenarios/not-real/contract')

    assert response.status_code == 404
    assert response.json()['detail'] == 'Benchmark scenario not found.'


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


def test_runs_export_returns_owner_scoped_history_bundle_with_vcon_summary():
    first = client.post(
        '/api/benchmarks/run',
        json={
            'user_id': 'demo-user',
            'project_id': 'qa-project',
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'billing-address-change',
            'transcript': 'Customer: Update my address. Agent: verified identity and updated billing address.',
            'action_trace': [{'action': 'verify identity', 'status': 'completed'}, {'action': 'update billing address', 'status': 'completed'}],
            'final_state': {'complete': True, 'billing_address_updated': True},
        },
    )
    second = client.post(
        '/api/benchmarks/run',
        json={
            'user_id': 'demo-user',
            'project_id': 'qa-project',
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'angry-outage-escalation',
            'transcript': 'Agent: I am sorry. I checked outage status, created ticket ABC, and escalated to a representative.',
            'action_trace': [{'action': 'check outage status', 'status': 'completed'}, {'action': 'escalate to representative', 'status': 'completed'}],
            'final_state': {'complete': True, 'escalated': True},
        },
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    export_response = client.get(
        '/api/benchmarks/runs/export',
        params={'user_id': 'demo-user', 'project_id': 'qa-project', 'suite_id': 'call-center-voice-ai'},
    )

    assert export_response.status_code == 200
    exported = export_response.json()
    assert exported['filename'] == 'agentbench-qa-project-call-center-voice-ai-benchmark-history.json'
    assert exported['run_count'] == 2
    assert exported['summary']['status_counts'] == {'completed': 2}
    assert exported['summary']['latest_run_id'] == second.json()['run_id']
    assert exported['summary']['latest_score'] == second.json()['overall_score']
    assert exported['summary']['previous_score'] == first.json()['overall_score']
    assert exported['summary']['latest_delta'] == second.json()['overall_score'] - first.json()['overall_score']
    assert exported['summary']['latest_trend'] in {'improved', 'regressed', 'unchanged'}
    assert exported['summary']['failure_category_counts'] == {'required_action_execution': 2}
    assert exported['summary']['top_failure_categories'] == [{'category': 'required_action_execution', 'count': 2}]
    assert exported['vcon_export_summary']['available_records'] == 2
    assert exported['vcon_export_summary']['analysis_records'] == 2
    assert exported['contract_artifact_summary'] == {
        'available_records': 2,
        'missing_records': 0,
        'total_runs': 2,
        'suite_contract_manifest_sha256s': [first.json()['suite_contract_manifest_sha256']],
        'scenario_contract_sha256s': sorted([
            first.json()['scenario_contract_sha256'],
            second.json()['scenario_contract_sha256'],
        ]),
    }
    assert exported['scenario_coverage_summary'] == {
        'suite_id': 'call-center-voice-ai',
        'scenario_count': 4,
        'covered_scenario_count': 2,
        'coverage_percent': 50.0,
        'covered_scenario_ids': ['billing-address-change', 'angry-outage-escalation'],
        'missing_scenario_ids': ['interruption-correction-handling', 'refund-policy-boundary'],
        'covered_scenarios': [
            {'id': 'billing-address-change', 'title': 'Billing Address Change'},
            {'id': 'angry-outage-escalation', 'title': 'Angry Outage Escalation'},
        ],
        'missing_scenarios': [
            {'id': 'interruption-correction-handling', 'title': 'Interruption and Correction Handling'},
            {'id': 'refund-policy-boundary', 'title': 'Refund Policy Boundary'},
        ],
        'recommended_next_scenario': {
            'id': 'interruption-correction-handling',
            'title': 'Interruption and Correction Handling',
        },
        'coverage_status': 'partial',
    }
    assert {run['run_id'] for run in exported['runs']} == {first.json()['run_id'], second.json()['run_id']}

    wrong_owner = client.get(
        '/api/benchmarks/runs/export',
        params={'user_id': 'other-user', 'project_id': 'qa-project', 'suite_id': 'call-center-voice-ai'},
    )

    assert wrong_owner.status_code == 200
    assert wrong_owner.json()['run_count'] == 0


def test_runs_export_summarizes_scenario_failure_categories():
    passing = client.post(
        '/api/benchmarks/run',
        json={
            'user_id': 'demo-user',
            'project_id': 'qa-project',
            'suite_id': 'telehealth-agent',
            'scenario_id': 'new-patient-triage',
            'transcript': 'Agent: I collected your date of birth, checked urgent symptoms, scheduled a telehealth appointment, and explained privacy consent.',
            'action_trace': [
                {'action': 'collect patient name and date of birth', 'status': 'completed'},
                {'action': 'ask about urgent symptoms', 'status': 'completed'},
                {'action': 'schedule telehealth appointment', 'status': 'completed'},
                {'action': 'explain privacy consent', 'status': 'completed'},
                {'action': 'avoid medical diagnosis', 'status': 'completed'},
            ],
            'final_state': {'complete': True, 'scheduled': True},
        },
    )
    failing = client.post(
        '/api/benchmarks/run',
        json={
            'user_id': 'demo-user',
            'project_id': 'qa-project',
            'suite_id': 'telehealth-agent',
            'scenario_id': 'new-patient-triage',
            'transcript': 'Agent: I can diagnose condition and recommend prescription medication.',
            'action_trace': [{'action': 'diagnose condition', 'status': 'completed'}],
            'final_state': {'complete': False},
        },
    )

    assert passing.status_code == 200, passing.text
    assert failing.status_code == 200, failing.text

    export_response = client.get(
        '/api/benchmarks/runs/export',
        params={'user_id': 'demo-user', 'project_id': 'qa-project', 'suite_id': 'telehealth-agent', 'scenario_id': 'new-patient-triage'},
    )

    assert export_response.status_code == 200, export_response.text
    summary = export_response.json()['summary']
    assert summary['latest_run_id'] == failing.json()['run_id']
    assert summary['latest_trend'] == 'regressed'
    assert summary['failure_category_counts'] == {
        'final_state_correctness': 1,
        'forbidden_action_avoidance': 1,
        'required_action_execution': 1,
        'task_completion': 1,
    }
    assert summary['top_failure_categories'][0] == {'category': 'final_state_correctness', 'count': 1}


def test_run_endpoint_accepts_vcon_record_evidence():
    response = client.post(
        '/api/benchmarks/run',
        json={
            'user_id': 'demo-user',
            'project_id': 'qa-project',
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'angry-outage-escalation',
            'vcon': {
                'vcon': '0.0.1',
                'parties': [{'name': 'Caller'}, {'name': 'Agent'}],
                'dialog': [
                    {'party': 0, 'body': 'This outage is frustrating and I want a human.'},
                    {'party': 1, 'body': 'I am sorry. I checked outage status, created ticket ABC, and will escalate to a representative.'},
                ],
            },
        },
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run['verdict'] == 'pass'
    assert run['evidence_audit_summary']['input_artifact_types'] == ['vcon']
    assert run['vcon_export']['source_format'] == 'vcon'
    assert run['vcon_export']['analysis'][-1]['type'] == 'agentic_benchmark_eval'


def test_run_audit_artifact_view_endpoint_returns_operator_evidence_bundle():
    response = client.post(
        '/api/benchmarks/run',
        json={
            'user_id': 'demo-user',
            'project_id': 'qa-project',
            'suite_id': 'fintech-support-agent',
            'scenario_id': 'suspicious-card-charge',
            'transcript': 'Agent: I will verify your account identity and file a fraud dispute case.',
            'action_trace': [{'action': 'verify account identity', 'status': 'completed'}],
            'final_state': {'complete': True, 'case_id': 'FRD-1001'},
            'agent_version': 'agent-v12',
            'prompt_version': 'prompt-2026-05-25',
        },
    )

    assert response.status_code == 200, response.text
    run = response.json()

    artifact_response = client.get(
        f"/api/benchmarks/runs/{run['run_id']}/audit-artifacts",
        params={'user_id': 'demo-user'},
    )

    assert artifact_response.status_code == 200, artifact_response.text
    payload = artifact_response.json()
    assert payload['filename'] == f"agentbench-fintech-support-agent-suspicious-card-charge-{run['run_id']}-audit-artifacts.json"
    assert payload['operator_summary'] == {
        'verdict': run['verdict'],
        'overall_score': run['overall_score'],
        'ready_for_export': True,
        'missing_export_artifacts': [],
        'artifact_count': 3,
        'evaluator_version': 'deterministic-agentic-v1',
    }
    assert payload['evidence_fingerprint'] == run['evidence_artifacts']['evidence_fingerprint']
    assert [artifact['type'] for artifact in payload['evidence_artifacts']] == ['transcript_text', 'action_trace', 'final_state']
    assert payload['audit_summary']['input_artifact_types'] == ['transcript', 'action_trace', 'final_state']
    assert payload['run_lifecycle']['status'] == run['run_status']
    assert payload['contract_artifact']['sha256'] == run['scenario_contract_sha256']
    assert payload['report_artifact']['type'] == 'deterministic_report'
    assert len(payload['report_artifact']['sha256']) == 64
    assert payload['report_artifact']['size_bytes'] > 0

    wrong_owner = client.get(
        f"/api/benchmarks/runs/{run['run_id']}/audit-artifacts",
        params={'user_id': 'other-user'},
    )

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


def test_suite_simulate_endpoint_persists_retained_suite_run_and_child_reports():
    payload = {
        'user_id': 'demo-user',
        'project_id': 'qa-project',
        'suite_id': 'call-center-voice-ai',
        'agent_profile': 'deterministic qa agent',
        'metadata': {'retention_days': '45'},
    }

    first = client.post('/api/benchmarks/suites/call-center-voice-ai/simulate', json=payload)
    second = client.post('/api/benchmarks/suites/call-center-voice-ai/simulate', json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    simulation = first.json()
    assert simulation['suite_run_id'] == second.json()['suite_run_id']

    list_response = client.get(
        '/api/benchmarks/suite-runs',
        params={'user_id': 'demo-user', 'project_id': 'qa-project', 'suite_id': 'call-center-voice-ai'},
    )

    assert list_response.status_code == 200
    suite_records = list_response.json()
    assert len(suite_records) == 1
    suite_record = suite_records[0]
    assert suite_record['suite_run_id'] == simulation['suite_run_id']
    assert suite_record['status'] == 'completed'
    assert suite_record['suite_report']['verdict'] == simulation['verdict']
    assert suite_record['reliability_metrics']['pass_at_1'] == 1.0
    assert suite_record['suite_report']['reliability_metrics']['framework'] == 'eva_bench_inspired_v1'
    expected_suite_manifest_sha = get_suite_contract_manifest('call-center-voice-ai')['suite_contract_manifest_sha256']
    assert suite_record['suite_contract_manifest_sha256'] == expected_suite_manifest_sha
    assert suite_record['suite_report']['suite_contract_manifest_sha256'] == expected_suite_manifest_sha
    assert suite_record['artifacts']['suite_contract_manifest_sha256'] == expected_suite_manifest_sha
    assert suite_record['scenario_count'] == simulation['scenario_count']
    assert suite_record['progress'] == {
        'phase': 'finished',
        'active': False,
        'completed_scenarios': simulation['scenario_count'],
        'total_scenarios': simulation['scenario_count'],
        'percent': 100,
    }
    assert suite_record['retention']['retention_days'] == 45
    assert suite_record['artifacts']['vcon_export'] == {
        'available': True,
        'dialog_turns': 0,
        'analysis_count': 1,
        'source_format': 'benchmark_suite',
        'appended_analysis_type': 'agentic_benchmark_suite_eval',
    }
    assert [item['scenario_id'] for item in suite_record['artifacts']['scenario_summaries']] == [
        run['benchmark_report']['scenario_id'] for run in simulation['scenario_runs']
    ]
    assert suite_record['suite_report']['vcon_export']['analysis'][0]['type'] == 'agentic_benchmark_suite_eval'

    detail_response = client.get(
        f"/api/benchmarks/suite-runs/{simulation['suite_run_id']}",
        params={'user_id': 'demo-user'},
    )

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload['suite_report']['suite_id'] == 'call-center-voice-ai'
    assert detail_payload['artifacts']['scenario_summaries'][0]['run_id'] == simulation['scenario_runs'][0]['benchmark_report']['run_id']

    export_response = client.get(
        f"/api/benchmarks/suite-runs/{simulation['suite_run_id']}/vcon-bundle",
        params={'user_id': 'demo-user'},
    )

    assert export_response.status_code == 200
    export_payload = export_response.json()
    assert export_payload['filename'] == f"agentbench-call-center-voice-ai-{simulation['suite_run_id']}-vcon-bundle.json"
    assert export_payload['record_count'] == simulation['scenario_count'] + 1
    assert export_payload['records'][0]['source_format'] == 'benchmark_suite'
    assert {record['appended_analysis_type'] for record in export_payload['records']} == {
        'agentic_benchmark_suite_eval',
        'agentic_benchmark_eval',
    }

    history_export_response = client.get(
        '/api/benchmarks/suite-runs/export',
        params={'user_id': 'demo-user', 'project_id': 'qa-project', 'suite_id': 'call-center-voice-ai'},
    )

    assert history_export_response.status_code == 200
    history_export = history_export_response.json()
    assert history_export['filename'] == 'agentbench-qa-project-call-center-voice-ai-suite-run-history.json'
    assert history_export['suite_run_count'] == 1
    assert history_export['summary']['latest_suite_run_id'] == simulation['suite_run_id']
    assert history_export['summary']['status_counts'] == {'completed': 1}
    assert history_export['summary']['total_scenarios'] == simulation['scenario_count']
    assert history_export['vcon_export_summary']['available_records'] == simulation['scenario_count'] + 1
    assert history_export['suite_contract_artifact_summary'] == {
        'available_records': 1,
        'missing_records': 0,
        'total_runs': 1,
        'suite_contract_manifest_sha256s': [expected_suite_manifest_sha],
        'scenario_contract_sha256s': sorted([
            run['benchmark_report']['scenario_contract_sha256'] for run in simulation['scenario_runs']
        ]),
    }
    assert history_export['suite_runs'][0]['suite_run_id'] == simulation['suite_run_id']

    run_history_export_response = client.get(
        '/api/benchmarks/runs/export',
        params={'user_id': 'demo-user', 'project_id': 'qa-project', 'suite_id': 'call-center-voice-ai'},
    )

    assert run_history_export_response.status_code == 200
    run_history_export = run_history_export_response.json()
    assert run_history_export['scenario_coverage_summary']['covered_scenario_count'] == simulation['scenario_count']
    assert run_history_export['scenario_coverage_summary']['coverage_percent'] == 100.0
    assert run_history_export['scenario_coverage_summary']['missing_scenario_ids'] == []
    assert run_history_export['scenario_coverage_summary']['recommended_next_scenario'] is None
    assert run_history_export['scenario_coverage_summary']['coverage_status'] == 'complete'

    audit_export_response = client.get(
        f"/api/benchmarks/suite-runs/{simulation['suite_run_id']}/audit-artifacts",
        params={'user_id': 'demo-user'},
    )

    assert audit_export_response.status_code == 200
    audit_export = audit_export_response.json()
    assert audit_export['filename'] == f"agentbench-call-center-voice-ai-{simulation['suite_run_id']}-suite-audit-artifacts.json"
    assert audit_export['operator_summary']['ready_for_export'] is True
    assert audit_export['operator_summary']['ready_scenarios'] == simulation['scenario_count']
    assert audit_export['operator_summary']['missing_scenarios'] == 0
    assert audit_export['suite_contract_artifact']['sha256'] == expected_suite_manifest_sha
    assert audit_export['report_artifact']['type'] == 'deterministic_suite_report'
    assert len(audit_export['report_artifact']['sha256']) == 64
    assert len(audit_export['scenario_artifacts']) == simulation['scenario_count']
    assert {artifact['scenario_id'] for artifact in audit_export['scenario_artifacts']} == {
        run['benchmark_report']['scenario_id'] for run in simulation['scenario_runs']
    }
    assert all(artifact['ready_for_export'] for artifact in audit_export['scenario_artifacts'])

    missing_audit_export = client.get(
        f"/api/benchmarks/suite-runs/{simulation['suite_run_id']}/audit-artifacts",
        params={'user_id': 'other-user'},
    )
    assert missing_audit_export.status_code == 404

    missing_export = client.get(
        f"/api/benchmarks/suite-runs/{simulation['suite_run_id']}/vcon-bundle",
        params={'user_id': 'other-user'},
    )
    assert missing_export.status_code == 404

    child_response = client.get(
        '/api/benchmarks/runs',
        params={'user_id': 'demo-user', 'project_id': 'qa-project', 'suite_id': 'call-center-voice-ai'},
    )

    assert child_response.status_code == 200
    child_records = child_response.json()
    assert len(child_records) == simulation['scenario_count']
    assert {record['run_id'] for record in child_records} == {
        run['benchmark_report']['run_id'] for run in simulation['scenario_runs']
    }

    saved_run_id = simulation['scenario_runs'][0]['benchmark_report']['run_id']
    run_export_response = client.get(
        f'/api/benchmarks/runs/{saved_run_id}/vcon',
        params={'user_id': 'demo-user'},
    )

    assert run_export_response.status_code == 200
    run_export = run_export_response.json()
    assert run_export['filename'] == f'agentbench-call-center-voice-ai-billing-address-change-{saved_run_id}-vcon.json'
    assert run_export['record']['appended_analysis_type'] == 'agentic_benchmark_eval'
    assert run_export['record']['analysis'][-1]['body']['run_id'] == saved_run_id

    missing_run_export = client.get(
        f'/api/benchmarks/runs/{saved_run_id}/vcon',
        params={'user_id': 'other-user'},
    )
    assert missing_run_export.status_code == 404

    wrong_owner = client.get(f"/api/benchmarks/suite-runs/{simulation['suite_run_id']}", params={'user_id': 'other-user'})
    assert wrong_owner.status_code == 404



def test_suite_run_history_export_includes_regression_trend_and_pass_rate():
    passing = client.post(
        '/api/benchmarks/suites/call-center-voice-ai/simulate',
        json={
            'user_id': 'demo-user',
            'project_id': 'qa-project',
            'agent_profile': 'deterministic qa agent',
            'prompt_version': 'prompt-pass',
        },
    )
    failing = client.post(
        '/api/benchmarks/suites/call-center-voice-ai/simulate',
        json={
            'user_id': 'demo-user',
            'project_id': 'qa-project',
            'agent_profile': 'deterministic qa agent',
            'prompt_version': 'prompt-regression',
            'include_failure': True,
        },
    )

    assert passing.status_code == 200, passing.text
    assert failing.status_code == 200, failing.text

    export_response = client.get(
        '/api/benchmarks/suite-runs/export',
        params={'user_id': 'demo-user', 'project_id': 'qa-project', 'suite_id': 'call-center-voice-ai'},
    )

    assert export_response.status_code == 200, export_response.text
    summary = export_response.json()['summary']
    assert summary['latest_suite_run_id'] == failing.json()['suite_run_id']
    assert summary['latest_average_score'] == failing.json()['average_score']
    assert summary['previous_average_score'] == passing.json()['average_score']
    assert summary['latest_delta'] == failing.json()['average_score'] - passing.json()['average_score']
    assert summary['latest_trend'] == 'regressed'
    assert summary['active_suite_runs'] == 0
    assert summary['terminal_suite_runs'] == 2
    assert summary['pass_rate'] == round((summary['total_passes'] / summary['total_scenarios']) * 100, 2)
    assert summary['failure_category_counts'] == {
        'final_state_correctness': failing.json()['scenario_count'],
        'forbidden_action_avoidance': failing.json()['scenario_count'],
        'required_action_execution': failing.json()['scenario_count'],
        'task_completion': failing.json()['scenario_count'],
    }
    assert summary['top_failure_categories'][0] == {'category': 'final_state_correctness', 'count': failing.json()['scenario_count']}
    coverage = export_response.json()['scenario_coverage_summary']
    assert coverage['scenario_count'] == passing.json()['scenario_count']
    assert coverage['covered_scenario_count'] == passing.json()['scenario_count']
    assert coverage['coverage_percent'] == 100.0
    assert coverage['missing_scenario_ids'] == []
    assert coverage['recommended_next_scenario'] is None
    assert coverage['coverage_status'] == 'complete'

    with SessionLocal() as db:
        create_benchmark_suite_run_record(
            db,
            suite_run_id='suite-active-export-1',
            suite_id='call-center-voice-ai',
            metadata={'user_id': 'demo-user', 'project_id': 'qa-project'},
        )

    running_export_response = client.get(
        '/api/benchmarks/suite-runs/export',
        params={'user_id': 'demo-user', 'project_id': 'qa-project', 'suite_id': 'call-center-voice-ai', 'status': 'queued'},
    )
    assert running_export_response.status_code == 200, running_export_response.text
    running_summary = running_export_response.json()['summary']
    assert running_summary['active_suite_runs'] == 1
    assert running_summary['terminal_suite_runs'] == 0


def test_suite_simulate_async_endpoint_tracks_queued_to_terminal_lifecycle():
    response = client.post(
        '/api/benchmarks/suites/call-center-voice-ai/simulate-async',
        json={
            'user_id': 'demo-user',
            'project_id': 'qa-project',
            'agent_profile': 'deterministic qa agent',
            'metadata': {'retention_days': '45'},
        },
    )

    assert response.status_code == 200, response.text
    queued = response.json()
    assert queued['status'] == 'queued'
    assert queued['scenario_count'] == len(get_suite('call-center-voice-ai')['scenarios'])
    assert queued['suite_contract_manifest_sha256'] == get_suite_contract_manifest('call-center-voice-ai')['suite_contract_manifest_sha256']
    assert queued['suite_report']['suite_contract_manifest_sha256'] == queued['suite_contract_manifest_sha256']
    assert queued['run_lifecycle']['status'] == 'queued'
    assert queued['run_lifecycle']['terminal'] is False
    assert queued['progress'] == {
        'phase': 'waiting',
        'active': True,
        'completed_scenarios': 0,
        'total_scenarios': len(get_suite('call-center-voice-ai')['scenarios']),
        'percent': 0,
    }
    assert queued['retention']['retention_days'] == 45

    detail_response = client.get(f"/api/benchmarks/suite-runs/{queued['suite_run_id']}", params={'user_id': 'demo-user'})

    assert detail_response.status_code == 200
    completed = detail_response.json()
    assert completed['suite_run_id'] == queued['suite_run_id']
    assert completed['status'] == 'completed'
    assert completed['suite_report']['suite_run_id'] == queued['suite_run_id']
    assert completed['run_lifecycle']['status'] == 'completed'
    assert completed['run_lifecycle']['terminal'] is True
    assert completed['progress']['phase'] == 'finished'
    assert completed['progress']['percent'] == 100
    assert completed['progress']['active'] is False
    assert [transition['to'] for transition in completed['run_lifecycle']['transitions']] == ['queued', 'running', 'completed']
    assert completed['scenario_count'] == len(get_suite('call-center-voice-ai')['scenarios'])


def test_suite_async_endpoint_retains_background_failures():
    response = client.post(
        '/api/benchmarks/suites/missing/simulate-async',
        json={'user_id': 'demo-user', 'project_id': 'qa-project'},
    )

    assert response.status_code == 200, response.text
    queued = response.json()

    detail_response = client.get(f"/api/benchmarks/suite-runs/{queued['suite_run_id']}", params={'user_id': 'demo-user'})

    assert detail_response.status_code == 200
    failed = detail_response.json()
    assert failed['status'] == 'failed'
    assert failed['completed_at'] is not None
    assert failed['progress']['phase'] == 'finished'
    assert failed['progress']['percent'] == 100
    assert failed['suite_report']['error'] == 'Unknown benchmark suite: missing'
    assert [transition['to'] for transition in failed['run_lifecycle']['transitions']] == ['queued', 'running', 'failed']


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
    assert len(result['suite_contract_manifest_sha256']) == 64
    assert result['suite_contract_manifest_sha256'] == get_suite_contract_manifest('fintech-support-agent')['suite_contract_manifest_sha256']
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
            'perturbation_tags': ['Noise', 'accent'],
        }
    )

    assert result['suite_id'] == 'telehealth-agent'
    assert result['suite_contract_manifest_sha256'] == get_suite_contract_manifest('telehealth-agent')['suite_contract_manifest_sha256']
    assert result['scenario_count'] == len(suite['scenarios'])
    assert result['pass_count'] == len(suite['scenarios'])
    assert result['needs_review_count'] == 0
    assert result['average_score'] >= 75
    assert result['verdict'] == 'pass'
    assert result['reliability_metrics']['framework'] == 'eva_bench_inspired_v1'
    assert result['reliability_metrics']['pass_at_1'] == 1.0
    assert result['reliability_metrics']['pass_at_k'] == 1.0
    assert result['reliability_metrics']['pass_all_k'] == 1.0
    assert result['reliability_metrics']['experience_signal_coverage'] == 1.0
    assert result['reliability_metrics']['perturbation_tags'] == ['accent', 'noise']
    assert result['reliability_metrics']['perturbation_coverage'] == [
        {'tag': 'accent', 'scenario_count': 2, 'pass_count': 2, 'pass_rate': 1.0},
        {'tag': 'noise', 'scenario_count': 2, 'pass_count': 2, 'pass_rate': 1.0},
    ]
    assert result['scenario_reports'][0]['perturbation_tags'] == ['noise', 'accent']
    assert result['run_metadata'] == {'agent_version': 'agent-v7'}
    assert [report['scenario_id'] for report in result['scenario_reports']] == [scenario['id'] for scenario in suite['scenarios']]
    suite_export = result['vcon_export']
    suite_analysis = suite_export['analysis'][0]
    assert suite_export['source_format'] == 'benchmark_suite'
    assert suite_export['appended_analysis_type'] == 'agentic_benchmark_suite_eval'
    assert suite_analysis['type'] == 'agentic_benchmark_suite_eval'
    assert suite_analysis['body']['suite_run_id'] == result['suite_run_id']
    assert suite_analysis['body']['suite_contract_manifest_sha256'] == result['suite_contract_manifest_sha256']
    assert suite_analysis['body']['scenario_count'] == result['scenario_count']
    assert suite_analysis['body']['reliability_metrics']['pass_at_1'] == 1.0
    assert suite_analysis['body']['reliability_metrics']['perturbation_tags'] == ['accent', 'noise']
    assert suite_analysis['body']['scenario_results'][0]['scenario_contract_sha256'] == result['scenario_reports'][0]['scenario_contract_sha256']
    assert suite_analysis['body']['scenario_results'][0]['perturbation_tags'] == ['noise', 'accent']


def test_run_suite_preserves_scenario_metadata_over_suite_defaults():
    suite = get_suite('telehealth-agent')
    assert suite is not None
    scenario_evidence = {}
    for index, scenario in enumerate(suite['scenarios'], start=1):
        simulation = simulate_scenario(
            {
                'suite_id': 'telehealth-agent',
                'scenario_id': scenario['id'],
                'agent_version': f'scenario-agent-v{index}',
            }
        )
        scenario_evidence[scenario['id']] = {
            'transcript': simulation['transcript'],
            'action_trace': simulation['action_trace'],
            'final_state': simulation['final_state'],
            'metadata': {'agent_version': f'scenario-agent-v{index}'},
        }

    result = run_suite(
        {
            'suite_id': 'telehealth-agent',
            'scenario_evidence': scenario_evidence,
            'metadata': {'agent_version': 'suite-agent-default', 'model_name': 'gpt-4.1-mini'},
        }
    )

    assert result['run_metadata'] == {'agent_version': 'suite-agent-default', 'model_name': 'gpt-4.1-mini'}
    assert [report['run_metadata'] for report in result['scenario_reports']] == [
        {'agent_version': 'scenario-agent-v1', 'model_name': 'gpt-4.1-mini'},
        {'agent_version': 'scenario-agent-v2', 'model_name': 'gpt-4.1-mini'},
    ]


def test_run_suite_accepts_attempt_arrays_for_retry_level_reliability():
    suite = get_suite('telehealth-agent')
    assert suite is not None
    scenario_attempts = {}
    for scenario in suite['scenarios']:
        passing = simulate_scenario(
            {
                'suite_id': 'telehealth-agent',
                'scenario_id': scenario['id'],
                'agent_version': 'agent-retry-v1',
            }
        )
        scenario_attempts[scenario['id']] = [
            {
                'transcript': 'Agent: I skipped required validation.',
                'action_trace': [],
                'final_state': {'complete': False},
                'perturbation_tags': ['noise'],
            },
            {
                'transcript': passing['transcript'],
                'action_trace': passing['action_trace'],
                'final_state': passing['final_state'],
                'perturbation_tags': ['noise'],
            },
        ]

    result = run_suite(
        {
            'suite_id': 'telehealth-agent',
            'scenario_attempts': scenario_attempts,
            'agent_version': 'agent-retry-v1',
        }
    )

    assert result['scenario_count'] == len(suite['scenarios']) * 2
    assert result['reliability_metrics']['scenario_count'] == len(suite['scenarios'])
    assert result['reliability_metrics']['attempt_count'] == len(suite['scenarios']) * 2
    assert result['reliability_metrics']['pass_at_1'] == 0.0
    assert result['reliability_metrics']['pass_at_k'] == 1.0
    assert result['reliability_metrics']['pass_all_k'] == 0.0
    assert result['reliability_metrics']['perturbation_coverage'] == [
        {'tag': 'noise', 'scenario_count': 4, 'pass_count': 2, 'pass_rate': 0.5},
    ]
    first_attempt = result['scenario_reports'][0]
    retry_attempt = result['scenario_reports'][1]
    assert first_attempt['run_lifecycle']['attempt'] == 1
    assert first_attempt['run_lifecycle']['max_attempts'] == 2
    assert retry_attempt['run_lifecycle']['attempt'] == 2
    assert retry_attempt['run_lifecycle']['retry_of_run_id'] == first_attempt['run_id']


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
    assert result['simulation_validation'] == {
        'status': 'ready_for_scoring',
        'ready_for_scoring': True,
        'artifact_presence': {'transcript': True, 'action_trace': True, 'final_state': True},
        'required_action_count': 5,
        'completed_required_action_count': 5,
        'missing_required_actions': [],
        'final_state_complete': True,
    }
    assert result['benchmark_report']['simulation_validation'] == result['simulation_validation']
    assert result['benchmark_report']['vcon_analysis']['body']['simulation_validation'] == result['simulation_validation']
    assert result['benchmark_report']['verdict'] == 'pass'
    assert result['benchmark_report']['overall_score'] >= 75


def test_simulate_scenario_flags_incomplete_generated_artifacts_for_regeneration():
    result = simulate_scenario(
        {
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'billing-address-change',
            'include_failure': True,
        }
    )

    validation = result['simulation_validation']
    assert validation['status'] == 'needs_regeneration'
    assert validation['ready_for_scoring'] is False
    assert validation['artifact_presence'] == {'transcript': True, 'action_trace': True, 'final_state': True}
    assert validation['missing_required_actions'] == ['explain next invoice impact']
    assert validation['completed_required_action_count'] == 4
    assert validation['final_state_complete'] is False
    assert result['benchmark_report']['simulation_validation'] == validation


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
    assert payload['vcon_export']['source_format'] == 'groupCall'
    assert payload['vcon_export']['parties'] == [{'name': 'caller'}, {'name': 'agent'}, {'name': 'supervisor'}]
    assert [turn['originator'] for turn in payload['vcon_export']['dialog']] == ['caller', 'agent', 'supervisor']
    assert payload['vcon_export']['analysis'][-1] == payload['vcon_analysis']


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
    assert payload['reliability_metrics']['pass_at_1'] == 1.0
    assert payload['reliability_metrics']['accuracy_score'] >= 0.75
    assert payload['run_metadata'] == {'agent_version': 'agent-v1'}
    assert payload['scenario_runs'][0]['benchmark_report']['suite_id'] == 'call-center-voice-ai'
    assert payload['vcon_export']['source_format'] == 'benchmark_suite'
    assert payload['vcon_export']['analysis'][0]['body']['suite_run_id'] == payload['suite_run_id']
    assert payload['vcon_export']['analysis'][0]['body']['reliability_metrics']['framework'] == 'eva_bench_inspired_v1'
    assert payload['vcon_export']['analysis'][0]['body']['scenario_results'][0]['run_id'] == payload['scenario_runs'][0]['benchmark_report']['run_id']


def test_simulate_suite_endpoint_rejects_unknown_suite():
    response = client.post('/api/benchmarks/suites/missing/simulate', json={})

    assert response.status_code == 404
    assert response.json()['detail'] == 'Unknown benchmark suite: missing'


def test_benchmark_history_coverage_preserves_custom_suite_scenarios():
    coverage = _history_scenario_coverage(
        records=[{"suite_id": "custom-suite", "scenario_id": "custom-refund-save"}],
        suite_id="custom-suite",
    )

    assert coverage == {
        "suite_id": "custom-suite",
        "scenario_count": None,
        "covered_scenario_count": 1,
        "coverage_percent": None,
        "covered_scenario_ids": ["custom-refund-save"],
        "missing_scenario_ids": [],
        "covered_scenarios": [{"id": "custom-refund-save", "title": "custom-refund-save"}],
        "missing_scenarios": [],
        "recommended_next_scenario": None,
        "coverage_status": "partial",
    }


def test_suite_history_coverage_preserves_custom_suite_scenarios():
    coverage = _suite_history_scenario_coverage(
        records=[{"suite_report": {"scenario_reports": [{"scenario_id": "custom-refund-save"}]}}],
        suite_id="custom-suite",
    )

    assert coverage == {
        "suite_id": "custom-suite",
        "scenario_count": None,
        "covered_scenario_count": 1,
        "coverage_percent": None,
        "covered_scenario_ids": ["custom-refund-save"],
        "missing_scenario_ids": [],
        "covered_scenarios": [{"id": "custom-refund-save", "title": "custom-refund-save"}],
        "missing_scenarios": [],
        "recommended_next_scenario": None,
        "coverage_status": "partial",
    }
