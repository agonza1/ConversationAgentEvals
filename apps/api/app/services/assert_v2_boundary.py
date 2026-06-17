from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.schemas.assert_v2 import (
    AssertResultManifest,
    AssertRunCreateRequest,
    AssertRuntimeConfig,
    AssertSuiteRunCreateRequest,
    PlatformRunRecord,
    PlatformSuiteRunRecord,
    PlatformSuiteScenarioRef,
)


ASSERT_V2_BOUNDARY_NAME = 'assert_v2_run_boundary'


def default_invocation_target(*, environment: str) -> dict[str, str | int]:
    base_url = 'http://assert-sidecar:8091' if environment == 'production' else 'http://127.0.0.1:8091'
    return {
        'transport': 'http_sidecar',
        'environment': environment,
        'base_url': base_url,
        'package_name': 'assert',
        'entrypoint': '/v2/runs',
        'timeout_seconds': 300,
    }


def queue_assert_run(request: AssertRunCreateRequest, *, platform_run_id: str, now: datetime | None = None) -> PlatformRunRecord:
    timestamp = _timestamp(now)
    return PlatformRunRecord(
        platform_run_id=platform_run_id,
        spec_ref=request.spec_ref,
        status='queued',
        created_at=timestamp,
        updated_at=timestamp,
        runtime_config=request.runtime_config,
        platform_metadata=request.platform_metadata,
        artifact_manifest=_input_artifacts(request),
        summary={
            'boundary': ASSERT_V2_BOUNDARY_NAME,
            'transport': request.runtime_config.invocation_target.transport,
            'execution_mode': request.runtime_config.execution_mode,
            'spec_id': request.spec_ref.spec_id,
            'spec_kind': request.spec_ref.spec_kind,
        },
    )


def ingest_assert_run_result(
    record: PlatformRunRecord,
    *,
    assert_run_id: str,
    result: AssertResultManifest,
    now: datetime | None = None,
) -> PlatformRunRecord:
    timestamp = _timestamp(now)
    return record.model_copy(
        update={
            'assert_run_id': assert_run_id,
            'status': 'completed' if result.verdict.status in {'pass', 'fail', 'needs_review'} else 'failed',
            'updated_at': timestamp,
            'completed_at': timestamp,
            'verdict': result.verdict,
            'failure_taxonomy': result.failures,
            'artifact_manifest': [*record.artifact_manifest, *result.artifacts],
            'audit_artifacts': {
                'ready_for_export': True,
                'missing_artifact_ids': [],
                'artifacts': [*record.artifact_manifest, *result.artifacts],
                'exports': list(result.summary_artifacts),
            },
            'summary': {
                **record.summary,
                'assert_run_id': assert_run_id,
                'verdict': result.verdict.status,
                'failure_count': len(result.failures),
            },
        }
    )


def queue_assert_suite_run(
    request: AssertSuiteRunCreateRequest,
    *,
    platform_suite_run_id: str,
    platform_run_ids: dict[str, str],
    now: datetime | None = None,
) -> PlatformSuiteRunRecord:
    timestamp = _timestamp(now)
    return PlatformSuiteRunRecord(
        platform_suite_run_id=platform_suite_run_id,
        spec_ref=request.spec_ref,
        status='queued',
        created_at=timestamp,
        updated_at=timestamp,
        runtime_config=request.runtime_config,
        platform_metadata=request.platform_metadata,
        scenarios=[
            PlatformSuiteScenarioRef(
                scenario_id=scenario.scenario_ref.spec_id,
                platform_run_id=platform_run_ids[scenario.scenario_ref.spec_id],
            )
            for scenario in request.scenarios
        ],
        summary={
            'boundary': ASSERT_V2_BOUNDARY_NAME,
            'transport': request.runtime_config.invocation_target.transport,
            'scenario_count': len(request.scenarios),
        },
    )


def with_default_runtime_config(runtime_config: AssertRuntimeConfig | None, *, environment: str) -> AssertRuntimeConfig:
    if runtime_config is not None:
        return runtime_config
    return AssertRuntimeConfig.model_validate(
        {
            'execution_mode': 'async',
            'invocation_target': default_invocation_target(environment=environment),
        }
    )


def legacy_execution_entrypoints() -> tuple[str, ...]:
    return (
        'app.routes.evals.run_voice_eval',
        'app.services.eval_service.run_eval',
        'app.services.benchmark_service.run_scenario',
        'app.services.benchmark_service.run_suite',
        'app.services.benchmark_service.simulate_scenario',
        'app.services.benchmark_service.simulate_suite',
    )


def recommended_v2_entrypoints() -> tuple[str, ...]:
    return (
        'create_assert_run(spec_ref, evidence, runtime_config, platform_metadata)',
        'create_assert_suite_run(spec_ref, scenarios, runtime_config, platform_metadata)',
        'ingest_assert_result(platform_run_id, assert_run_id, result_manifest)',
    )


def _input_artifacts(request: AssertRunCreateRequest) -> list[Any]:
    evidence = request.evidence
    artifacts = [
        evidence.transcript,
        evidence.conversation,
        evidence.vcon,
        evidence.action_trace,
        evidence.final_state,
        evidence.assert_bundle,
        *evidence.call_media,
        *evidence.additional_artifacts,
    ]
    return [artifact for artifact in artifacts if artifact is not None]


def _timestamp(now: datetime | None) -> str:
    current = now or datetime.now(UTC)
    return current.isoformat().replace('+00:00', 'Z')
