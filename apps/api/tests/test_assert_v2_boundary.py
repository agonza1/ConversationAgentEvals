from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.assert_v2 import AssertResultManifest, AssertRunCreateRequest, AssertSuiteRunCreateRequest
from app.services.assert_queue_lifecycle import (
    create_queue_state,
    enforce_cost_limit,
    mark_completed,
    mark_failed,
    mark_running,
    request_cancel,
    retry_from_failure,
)
from app.services.assert_v2_boundary import (
    ASSERT_V2_BOUNDARY_NAME,
    default_invocation_target,
    archival_pre_v2_data_policy,
    ingest_assert_run_result,
    queue_assert_run,
    queue_assert_suite_run,
    recommended_v2_entrypoints,
    with_default_runtime_config,
)


def _run_request() -> AssertRunCreateRequest:
    return AssertRunCreateRequest.model_validate(
        {
            'spec_ref': {
                'spec_id': 'telehealth-agent/medication-refill-routing',
                'spec_kind': 'scenario',
                'spec_version': '2026-06-17',
                'assert_project': 'conversation-agent-evals',
            },
            'evidence': {
                'conversation': {
                    'artifact_id': 'conversation-json',
                    'kind': 'conversation',
                    'inline_data': {'dialog': [{'speaker': 'Patient', 'body': 'I need a refill.'}]},
                },
                'action_trace': {
                    'artifact_id': 'tool-trace',
                    'kind': 'action_trace',
                    'inline_data': [{'action': 'verify patient identity', 'status': 'completed'}],
                },
                'final_state': {
                    'artifact_id': 'final-state',
                    'kind': 'final_state',
                    'inline_data': {'complete': True},
                },
                'provenance': {'source_run_id': 'incident-42'},
            },
            'runtime_config': {
                'execution_mode': 'async',
                'invocation_target': default_invocation_target(environment='local'),
            },
            'platform_metadata': {
                'user_id': 'user-1',
                'project_id': 'project-1',
                'root_run_id': 'root-1',
                'labels': ['nightly', 'migration'],
            },
        }
    )


def test_assert_v2_run_requires_version_or_hash():
    with pytest.raises(ValueError, match='spec_version or spec_hash is required'):
        AssertRunCreateRequest.model_validate(
            {
                'spec_ref': {'spec_id': 'spec-1', 'spec_kind': 'scenario'},
                'evidence': {
                    'transcript': {
                        'artifact_id': 't1',
                        'kind': 'transcript',
                        'inline_data': 'Agent: Hello',
                    }
                },
                'runtime_config': {
                    'execution_mode': 'async',
                    'invocation_target': default_invocation_target(environment='local'),
                },
                'platform_metadata': {'user_id': 'user-1', 'project_id': 'project-1'},
            }
        )


def test_queue_assert_run_creates_single_boundary_record():
    record = queue_assert_run(
        _run_request(),
        platform_run_id='platform-run-1',
        now=datetime(2026, 6, 17, 15, 0, tzinfo=UTC),
    )

    assert record.platform_run_id == 'platform-run-1'
    assert record.status == 'queued'
    assert record.summary == {
        'boundary': ASSERT_V2_BOUNDARY_NAME,
        'transport': 'http_sidecar',
        'execution_mode': 'async',
        'spec_id': 'telehealth-agent/medication-refill-routing',
        'spec_kind': 'scenario',
    }
    assert [artifact.artifact_id for artifact in record.artifact_manifest] == [
        'conversation-json',
        'tool-trace',
        'final-state',
    ]


def test_ingest_assert_run_result_persists_verdict_taxonomy_and_exports():
    queued = queue_assert_run(_run_request(), platform_run_id='platform-run-1', now=datetime(2026, 6, 17, 15, 0, tzinfo=UTC))
    result = AssertResultManifest.model_validate(
        {
            'verdict': {'status': 'fail', 'score': 62, 'summary': 'Missing clinician handoff'},
            'failures': [
                {
                    'code': 'missing-clinician-review',
                    'category': 'required_action',
                    'severity': 'error',
                    'summary': 'The run did not queue clinician review.',
                    'evidence_artifact_ids': ['tool-trace'],
                }
            ],
            'artifacts': [
                {
                    'artifact_id': 'assert-result',
                    'kind': 'report',
                    'role': 'output',
                    'uri': 's3://assert/results/platform-run-1.json',
                }
            ],
            'summary_artifacts': [
                {
                    'artifact_id': 'assert-vcon-export',
                    'kind': 'export',
                    'role': 'derived',
                    'uri': 's3://assert/exports/platform-run-1-vcon.json',
                }
            ],
        }
    )

    completed = ingest_assert_run_result(
        queued,
        assert_run_id='assert-run-1',
        result=result,
        now=datetime(2026, 6, 17, 15, 5, tzinfo=UTC),
    )

    assert completed.assert_run_id == 'assert-run-1'
    assert completed.status == 'completed'
    assert completed.verdict is not None
    assert completed.verdict.status == 'fail'
    assert completed.failure_taxonomy[0].code == 'missing-clinician-review'
    assert completed.audit_artifacts.ready_for_export is True
    assert [artifact.artifact_id for artifact in completed.audit_artifacts.exports] == ['assert-vcon-export']
    assert completed.summary['failure_count'] == 1


def test_suite_run_contract_requires_child_scenarios_and_tracks_platform_runs():
    base_request = _run_request()
    suite_request = AssertSuiteRunCreateRequest.model_validate(
        {
            'spec_ref': {
                'spec_id': 'telehealth-agent',
                'spec_kind': 'suite',
                'spec_version': '2026-06-17',
            },
            'scenarios': [
                {
                    'scenario_ref': base_request.spec_ref.model_dump(),
                    'evidence': base_request.evidence.model_dump(),
                    'platform_metadata': base_request.platform_metadata.model_dump(),
                }
            ],
            'runtime_config': base_request.runtime_config.model_dump(),
            'platform_metadata': base_request.platform_metadata.model_dump(),
        }
    )

    record = queue_assert_suite_run(
        suite_request,
        platform_suite_run_id='platform-suite-1',
        platform_run_ids={'telehealth-agent/medication-refill-routing': 'platform-run-1'},
        now=datetime(2026, 6, 17, 15, 0, tzinfo=UTC),
    )

    assert record.platform_suite_run_id == 'platform-suite-1'
    assert record.summary['scenario_count'] == 1
    assert record.scenarios[0].scenario_id == 'telehealth-agent/medication-refill-routing'
    assert record.scenarios[0].platform_run_id == 'platform-run-1'


def test_default_runtime_config_uses_http_sidecar_for_local_and_production():
    local = with_default_runtime_config(None, environment='local')
    production = with_default_runtime_config(None, environment='production')

    assert local.invocation_target.transport == 'http_sidecar'
    assert local.invocation_target.base_url == 'http://127.0.0.1:8091'
    assert production.invocation_target.base_url == 'http://assert-sidecar:8091'


def test_boundary_module_exposes_only_v2_entrypoints_and_archival_policy():
    assert recommended_v2_entrypoints() == (
        'create_assert_run(spec_ref, evidence, runtime_config, platform_metadata)',
        'create_assert_suite_run(spec_ref, scenarios, runtime_config, platform_metadata)',
        'ingest_assert_result(platform_run_id, assert_run_id, result_manifest)',
    )
    assert archival_pre_v2_data_policy() == {
        'classification': 'pre-v2 archival',
        'active_evaluator_input': 'false',
        'migration_policy': 'Historical records may be displayed or exported, but production run creation must use ASSERT v2 contracts.',
    }


def test_assert_queue_lifecycle_tracks_successful_worker_run():
    queued = create_queue_state(
        run_id='platform-run-1',
        max_attempts=2,
        cost_limit_usd=1.5,
        estimated_cost_usd=0.25,
        now=datetime(2026, 6, 17, 15, 0, tzinfo=UTC),
    )
    running = mark_running(queued, now=datetime(2026, 6, 17, 15, 1, tzinfo=UTC))
    completed = mark_completed(running, spent_cost_usd=0.2, now=datetime(2026, 6, 17, 15, 3, tzinfo=UTC))

    assert completed['status'] == 'completed'
    assert completed['terminal'] is True
    assert completed['spent_cost_usd'] == 0.2
    assert [transition['to'] for transition in completed['transitions']] == ['queued', 'running', 'completed']


def test_assert_queue_lifecycle_supports_retry_cancel_and_cost_limits():
    queued = create_queue_state(
        run_id='platform-run-2',
        max_attempts=2,
        cost_limit_usd=0.5,
        now=datetime(2026, 6, 17, 15, 0, tzinfo=UTC),
    )
    failed_for_cost = enforce_cost_limit(queued, estimated_cost_usd=0.75, now=datetime(2026, 6, 17, 15, 1, tzinfo=UTC))
    assert failed_for_cost['status'] == 'failed'
    assert failed_for_cost['retryable'] is False
    assert failed_for_cost['error'] == 'estimated ASSERT cost exceeds run cost limit'

    failed_worker = mark_failed(
        mark_running(create_queue_state(run_id='platform-run-3', max_attempts=2)),
        reason='sidecar timeout',
    )
    retry = retry_from_failure(failed_worker, now=datetime(2026, 6, 17, 15, 2, tzinfo=UTC))
    assert retry['status'] == 'queued'
    assert retry['attempt'] == 2
    assert retry['retry_parent_attempt'] == 1

    canceled = request_cancel(mark_running(retry), reason='user canceled run')
    assert canceled['status'] == 'canceled'
    assert canceled['terminal'] is True
    assert canceled['cancel_requested'] is True
    assert canceled['transitions'][-1]['reason'] == 'user canceled run'


def test_local_v2_runs_sidecar_ingests_acc_assert_request(tmp_path, monkeypatch):
    monkeypatch.setattr(
        'app.services.assert_sidecar.local_assert_sidecar_artifact_path',
        lambda platform_run_id, artifact_root=None: tmp_path / f'{platform_run_id}.json',
    )
    client = TestClient(app)
    request = _run_request().model_dump(mode='json')
    request['spec_ref'] = {
        'spec_id': 'agentic-contact-center/cancellation-rescue',
        'spec_kind': 'scenario',
        'spec_version': '2026-06-29',
        'assert_project': 'conversation-agent-evals',
    }
    request['runtime_config']['environment_labels'] = [
        'agentic-contact-center',
        'pipecat_local_runtime',
        'mocked-telephony',
    ]
    request['platform_metadata'].update(
        {
            'user_id': 'alberto-local-proof',
            'project_id': 'agentic-contact-center',
            'initiated_by': 'local-script',
        }
    )

    response = client.post('/v2/runs', json=request)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['status'] == 'completed'
    assert payload['assert_run_id'] == f"local-assert-{payload['platform_run_id']}"
    assert payload['spec_ref']['spec_id'] == 'agentic-contact-center/cancellation-rescue'
    assert payload['platform_metadata']['project_id'] == 'agentic-contact-center'
    assert payload['runtime_config']['invocation_target']['entrypoint'] == '/v2/runs'
    assert payload['verdict'] == {
        'status': 'pass',
        'score': 100.0,
        'summary': 'Local synthetic ASSERT sidecar accepted the run evidence for platform ingestion.',
        'metrics': {'input_artifact_count': 3, 'missing_input_artifact_count': 0},
    }
    assert payload['audit_artifacts']['ready_for_export'] is True
    assert [artifact['artifact_id'] for artifact in payload['artifact_manifest']][-1] == 'local-sidecar-result-manifest'
    saved_path = tmp_path / f"{payload['platform_run_id']}.json"
    assert saved_path.exists()

    detail = client.get(f"/v2/runs/{payload['platform_run_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()['platform_run_id'] == payload['platform_run_id']


def test_local_v2_runs_sidecar_rejects_legacy_payload():
    client = TestClient(app)

    response = client.post('/v2/runs', json={'conversation': 'Agent: hello'})

    assert response.status_code == 422
