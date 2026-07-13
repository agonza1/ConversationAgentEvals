from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.agentic_contact_center_example import build_benchmark_run_request, normalize_acc_run
from app.services.benchmark_catalog_extensions import register_builtin_benchmark_extensions
from app.services.benchmark_service import get_scenario_contract, list_suites, run_scenario


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
