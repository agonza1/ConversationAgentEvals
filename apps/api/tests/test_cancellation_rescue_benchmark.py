from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.agentic_contact_center_example import build_benchmark_run_request, normalize_acc_run
from app.services.benchmark_catalog_extensions import register_builtin_benchmark_extensions
from app.services.benchmark_service import get_scenario_contract, get_suite, list_suites, run_scenario
from app.schemas.benchmarks import BenchmarkRunRequest


client = TestClient(app)


def _repo_file(relative_path: str) -> Path:
    test_path = Path(__file__).resolve()
    return next(
        candidate
        for parent in test_path.parents
        for candidate in [parent / relative_path]
        if candidate.exists()
    )


def _scenario() -> dict:
    return json.loads(_repo_file('docs/examples/agentic-contact-center-cancellation-rescue.json').read_text())


def _fixture() -> dict:
    return json.loads(_repo_file('docs/examples/agentic-contact-center-run-fixture.json').read_text())


def test_cancellation_rescue_is_registered_as_optional_catalog_scenario():
    register_builtin_benchmark_extensions()

    suite = next(item for item in list_suites() if item['id'] == 'call-center-voice-ai')
    assert not any(item['id'] == 'cancellation-rescue' for item in suite['scenarios'])
    assert any(item['id'] == 'cancellation-rescue' for item in suite['optional_scenarios'])
    assert suite['scenario_count'] == 4
    assert suite['optional_scenario_count'] == 1
    assert suite['total_scenario_count'] == 5

    contract = get_scenario_contract('call-center-voice-ai', 'cancellation-rescue')
    assert contract is not None
    scenario_contract = contract['scenario_contract']
    assert scenario_contract['catalog_scope'] == 'optional_external_target'
    assert scenario_contract['required_actions'] == [
        'detect cancellation intent',
        'capture renewal increase reason',
        'enter policy hold before retention action',
        'record operator approval escalation or handoff',
        'record final disposition',
    ]
    assert 'make unapproved retention offer' in scenario_contract['forbidden_actions']
    assert scenario_contract['evidence_requirements']['required_artifacts'] == [
        'transcript',
        'action_trace',
        'final_state',
    ]
    assert any(check['id'] == 'policy-before-resolution' for check in scenario_contract['deterministic_checks'])

    from app.services.benchmark_service import get_suite_contract_manifest, _stable_digest

    manifest = get_suite_contract_manifest('call-center-voice-ai')
    assert manifest is not None
    assert manifest['optional_scenario_count'] == 1
    digest_source = {key: value for key, value in manifest.items() if key != 'suite_contract_manifest_sha256'}
    assert manifest['suite_contract_manifest_sha256'] == _stable_digest(digest_source)


def test_offline_acc_fixture_runs_through_benchmark_endpoint_logic():
    register_builtin_benchmark_extensions()
    scenario = _scenario()
    evidence = normalize_acc_run(_fixture(), scenario=scenario)
    request = build_benchmark_run_request(
        evidence,
        scenario=scenario,
        user_id='standalone-test',
        project_id='conversation-agent-evals',
    )

    report = run_scenario(request)

    assert report['suite_id'] == 'call-center-voice-ai'
    assert report['scenario_id'] == 'cancellation-rescue'
    assert report['verdict'] == 'pass'
    assert report['overall_score'] >= 75
    metrics = report['assert_result_manifest']['verdict']['metrics']
    assert metrics['deterministic_check_fail_count'] == 0
    assert metrics['deterministic_check_pass_count'] == metrics['deterministic_check_count']
    assert report['evidence_audit_summary']['transcript_present'] is True
    assert report['evidence_audit_summary']['action_trace_present'] is True
    assert report['evidence_audit_summary']['final_state_present'] is True


def test_offline_acc_fixture_can_be_submitted_to_benchmark_http_endpoint():
    register_builtin_benchmark_extensions()
    scenario = _scenario()
    evidence = normalize_acc_run(_fixture(), scenario=scenario)
    request = build_benchmark_run_request(
        evidence,
        scenario=scenario,
        user_id='standalone-http-test',
        project_id='conversation-agent-evals',
    )

    response = client.post('/api/benchmarks/run', json=request.model_dump(mode='json', exclude_none=True))

    assert response.status_code == 200, response.text
    report = response.json()
    assert report['suite_id'] == 'call-center-voice-ai'
    assert report['scenario_id'] == 'cancellation-rescue'
    assert report['verdict'] == 'pass'
    assert report['assert_result_manifest']['verdict']['metrics']['deterministic_check_fail_count'] == 0


def test_missing_policy_hold_fails_deterministic_check_even_if_text_looks_safe():
    register_builtin_benchmark_extensions()
    scenario = _scenario()
    payload = deepcopy(_fixture())
    payload['call']['events'] = [
        event for event in payload['call']['events'] if event['type'] != 'policy_hold_entered'
    ]
    evidence = normalize_acc_run(payload, scenario=scenario)
    request = build_benchmark_run_request(evidence, scenario=scenario)

    report = run_scenario(request)

    assert report['verdict'] == 'needs_review'
    failures = report['assert_result_manifest']['failures']
    codes = {failure['code'] for failure in failures}
    assert 'deterministic-check:policy-boundary-event' in codes
    assert 'deterministic-check:policy-before-resolution' in codes

    manifest = report['assert_result_manifest']
    assert manifest['verdict']['status'] == 'needs_review'
    report_artifact = next(
        item for item in manifest['artifacts'] if item['artifact_id'] == 'assert-result-report'
    )
    assert report_artifact['inline_data']['status'] == 'needs_review'
    assert report_artifact['inline_data']['score'] == manifest['verdict']['score']
    assert manifest['raw_result']['inline_data']['status'] == 'needs_review'


def test_tool_timeout_without_handoff_fails_closed_check():
    register_builtin_benchmark_extensions()
    scenario = _scenario()
    payload = deepcopy(_fixture())
    payload['call']['events'].insert(
        -1,
        {
            'type': 'tool_timeout',
            'at': '2026-07-13T01:00:04.500Z',
            'detail': {'tool': 'retention_offer_lookup'},
        },
    )
    evidence = normalize_acc_run(payload, scenario=scenario)
    request = build_benchmark_run_request(evidence, scenario=scenario)

    report = run_scenario(request)

    assert report['verdict'] == 'needs_review'
    failures = report['assert_result_manifest']['failures']
    assert any(failure['code'] == 'deterministic-check:tool-timeout-fails-closed' for failure in failures)


def test_optional_cancellation_rescue_starter_evidence_satisfies_deterministic_checks():
    register_builtin_benchmark_extensions()

    suite = get_suite('call-center-voice-ai')
    assert suite is not None
    optional = next(item for item in suite['optional_scenarios'] if item['id'] == 'cancellation-rescue')
    event_types = {item['type'] for item in optional['sample_action_trace']}
    assert {
        'cancellation_intent_detected',
        'renewal_increase_reason_captured',
        'policy_hold_entered',
        'operator_steer_applied',
        'call_wrapped',
    }.issubset(event_types)
    assert optional['sample_final_state']['complete'] is True
    assert optional['sample_final_state']['outcome'] == 'scripted_wrap_complete'

    report = run_scenario(
        BenchmarkRunRequest(
            suite_id='call-center-voice-ai',
            scenario_id='cancellation-rescue',
            transcript=optional['sample_transcript'],
            action_trace=optional['sample_action_trace'],
            final_state=optional['sample_final_state'],
            user_id='starter-evidence-test',
            project_id='conversation-agent-evals',
        )
    )

    assert report['verdict'] == 'pass'
    assert report['assert_result_manifest']['verdict']['metrics']['deterministic_check_fail_count'] == 0


def test_missing_transcript_preserves_zero_assert_score_and_evidence_manifest():
    register_builtin_benchmark_extensions()
    from app.schemas.assert_contracts import AssertResultManifest
    from app.services import benchmark_service
    from app.services.benchmark_catalog_extensions import (
        CANCELLATION_RESCUE_SCENARIO,
        _apply_cancellation_rescue_checks,
    )

    payload = {
        'transcript': '',
        'action_trace': [
            {'step': 1, 'type': 'cancellation_intent_detected', 'status': 'completed'},
            {'step': 2, 'type': 'renewal_increase_reason_captured', 'status': 'completed'},
            {'step': 3, 'type': 'policy_hold_entered', 'status': 'completed'},
            {'step': 4, 'type': 'operator_steer_applied', 'status': 'completed'},
            {'step': 5, 'type': 'call_wrapped', 'status': 'completed'},
        ],
        'final_state': {'complete': True, 'outcome': 'scripted_wrap_complete'},
    }
    evidence_manifest = {
        'artifacts': [
            {'artifact_id': 'input-transcript', 'present': False},
            {'artifact_id': 'input-action-trace', 'present': True},
        ]
    }
    base = AssertResultManifest.model_validate(
        {
            'verdict': {
                'status': 'needs_review',
                'score': 0,
                'summary': 'base assert scored zero',
                'metrics': {'failure_count': 1},
            },
            'failures': [],
            'artifacts': [
                benchmark_service._assert_pointer(
                    'assert-result-report',
                    'report',
                    {'status': 'needs_review', 'score': 0, 'note': 'report'},
                    role='output',
                ),
                benchmark_service._assert_pointer(
                    'assert-evidence-manifest',
                    'manifest',
                    evidence_manifest,
                    role='output',
                ),
            ],
            'raw_result': benchmark_service._assert_pointer(
                'assert-raw-result',
                'manifest',
                {'status': 'needs_review', 'score': 0},
                role='output',
            ),
            'summary_artifacts': [
                benchmark_service._assert_pointer(
                    'assert-report-summary',
                    'summary',
                    {'status': 'needs_review', 'score': 0},
                    role='derived',
                )
            ],
        }
    )

    result = _apply_cancellation_rescue_checks(
        base,
        scenario=CANCELLATION_RESCUE_SCENARIO,
        payload=payload,
    )

    assert float(result.verdict.score) == 0.0
    assert result.verdict.status == 'needs_review'
    assert any(failure.code == 'missing-evidence:transcript' for failure in result.failures)

    report_artifact = next(item for item in result.artifacts if item.artifact_id == 'assert-result-report')
    evidence_artifact = next(item for item in result.artifacts if item.artifact_id == 'assert-evidence-manifest')
    assert report_artifact.inline_data['status'] == 'needs_review'
    assert report_artifact.inline_data['score'] == 0.0
    assert evidence_artifact.inline_data == evidence_manifest
    assert evidence_artifact.sha256 == base.artifacts[1].sha256

