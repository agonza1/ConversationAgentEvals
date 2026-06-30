from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
API_APP = ROOT / 'apps' / 'api'
sys.path.insert(0, str(API_APP))

from app.main import app  # noqa: E402
from app.services.product_service import reset_saved_runs_for_tests  # noqa: E402


client = TestClient(app)


def main() -> None:
    reset_saved_runs_for_tests()

    suites = _json(_ok(client.get('/api/benchmarks/suites')))
    _assert(len(suites) >= 4, 'expected seeded benchmark suites')
    _assert({suite['id'] for suite in suites} >= {
        'call-center-voice-ai',
        'telehealth-agent',
        'online-teaching-agent',
        'fintech-support-agent',
    }, 'expected benchmark suite ids')
    _assert(all(suite.get('scenarios') for suite in suites), 'expected scenarios on every suite')

    pass_run = _json(_ok(client.post(
        '/api/benchmarks/simulate',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'billing-address-change',
            'agent_profile': 'smoke deterministic agent',
            'metadata': {
                'agentVersion': 'smoke-agent-v1',
                'promptVersion': 'smoke-prompt-v1',
                'modelName': 'deterministic-local',
                'notes': 'api smoke pass path',
            },
        },
    )))
    pass_report = pass_run['benchmark_report']
    _assert(pass_report['verdict'] == 'pass', 'expected pass simulation verdict')
    _assert(pass_report['overall_score'] >= 75, 'expected passing score')
    _assert(pass_report['run_metadata'] == {
        'agent_version': 'smoke-agent-v1',
        'prompt_version': 'smoke-prompt-v1',
        'model_name': 'deterministic-local',
        'notes': 'api smoke pass path',
    }, 'expected normalized run metadata')
    _assert_audit_summary(pass_report['evidence_audit_summary'])

    failure_run = _json(_ok(client.post(
        '/api/benchmarks/simulate',
        json={
            'suiteId': 'telehealth-agent',
            'scenarioId': 'medication-refill-routing',
            'include_failure': True,
            'agentProfile': 'smoke deterministic failure agent',
        },
    )))
    failure_report = failure_run['benchmark_report']
    _assert(failure_report['verdict'] == 'needs_review', 'expected failure simulation verdict')
    _assert(failure_report['overall_score'] < 75, 'expected failing score')
    _assert(failure_run['final_state']['missing_actions'] == ['state refill timing expectations'], 'expected final-state missing action evidence')
    _assert('approve refill directly' in failure_report['forbidden_actions_observed'], 'expected forbidden action evidence')

    saved = _json(_ok(client.post(
        '/api/product/runs',
        json={
            'user_id': 'smoke-user',
            'project_id': 'issue-5-human-behavior',
            'plan': 'starter',
            'report': pass_report,
            'transcript': pass_run['transcript'],
        },
    )))
    _assert(saved['id'], 'expected saved run id')
    _assert(saved['report']['run_id'] == pass_report['run_id'], 'expected saved report run id')

    history = _json(_ok(client.get(
        '/api/product/runs',
        params={'user_id': 'smoke-user', 'project_id': 'issue-5-human-behavior'},
    )))
    _assert(len(history) == 1, 'expected one saved history item')
    _assert(history[0]['id'] == saved['id'], 'expected saved run in history')
    _assert(history[0]['report']['run_metadata'] == pass_report['run_metadata'], 'expected history metadata')
    _assert(history[0]['report']['evidence_audit_summary'] == pass_report['evidence_audit_summary'], 'expected history audit summary')

    exported = _json(_ok(client.get(
        f"/api/product/runs/{saved['id']}/export",
        params={'user_id': 'smoke-user'},
    )))
    _assert(exported['id'] == saved['id'], 'expected export id')
    _assert(exported['filename'] == f"agentbench-issue-5-human-behavior-{saved['id']}.json", 'expected export filename')
    _assert(exported['project_id'] == 'issue-5-human-behavior', 'expected export project id')
    _assert(exported['report']['run_id'] == pass_report['run_id'], 'expected export report run id')
    _assert(exported['report']['run_metadata'] == pass_report['run_metadata'], 'expected export metadata')
    _assert(exported['report']['evidence_audit_summary']['export_readiness'] == {
        'ready': True,
        'format': 'assert_artifact_manifest',
        'missing': [],
    }, 'expected export-ready audit summary')
    _assert(exported['transcript'] == pass_run['transcript'], 'expected export transcript')

    print('benchmark smoke passed: suites, pass/failure simulations, metadata audit, history, and export shape verified')


def _ok(response: Any) -> Any:
    _assert(response.status_code < 400, f'{response.request.method} {response.request.url} returned {response.status_code}: {response.text}')
    return response


def _json(response: Any) -> Any:
    return response.json()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_audit_summary(summary: dict[str, Any]) -> None:
    _assert(bool(summary.get('run_started_at')), 'expected audit run_started_at')
    _assert(bool(summary.get('evaluated_at')), 'expected audit evaluated_at')
    _assert(summary.get('input_artifact_types') == ['transcript', 'action_trace', 'final_state'], 'expected audit artifact types')
    _assert(summary.get('transcript_present') is True, 'expected transcript audit flag')
    _assert(summary.get('action_trace_present') is True, 'expected action trace audit flag')
    _assert(summary.get('final_state_present') is True, 'expected final state audit flag')
    _assert(summary.get('metadata_labels') == ['agent_version', 'model_name', 'notes', 'prompt_version'], 'expected metadata audit labels')
    _assert(summary.get('evaluator_version') == 'assert-v2-boundary-v1', 'expected ASSERT v2 evaluator version')
    _assert(summary.get('export_readiness') == {
        'ready': True,
        'format': 'assert_artifact_manifest',
        'missing': [],
    }, 'expected export readiness')


if __name__ == '__main__':
    main()
