from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas.assert_v2 import AssertFailureItem, AssertResultManifest, AssertRunCreateRequest, AssertVerdict, PlatformRunRecord
from app.services.assert_v2_boundary import ingest_assert_run_result, queue_assert_run


SIDECAR_ADAPTER_VERSION = 'conversation-agent-evals-local-assert-sidecar-v1'


def create_local_assert_sidecar_run(request: AssertRunCreateRequest, *, artifact_root: Path | None = None) -> PlatformRunRecord:
    platform_run_id = _platform_run_id(request)
    queued = queue_assert_run(request, platform_run_id=platform_run_id)
    result = _result_manifest(request=request, platform_run_id=platform_run_id)
    completed = ingest_assert_run_result(
        queued,
        assert_run_id=f'local-assert-{platform_run_id}',
        result=result,
    )
    _persist_sidecar_record(completed, artifact_root=artifact_root)
    return completed


def local_assert_sidecar_artifact_path(platform_run_id: str, *, artifact_root: Path | None = None) -> Path:
    root = artifact_root or Path(__file__).resolve().parents[4] / 'artifacts' / 'assert-v2-sidecar'
    return root / f'{platform_run_id}.json'


def load_local_assert_sidecar_run(platform_run_id: str, *, artifact_root: Path | None = None) -> dict[str, Any] | None:
    path = local_assert_sidecar_artifact_path(platform_run_id, artifact_root=artifact_root)
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _result_manifest(*, request: AssertRunCreateRequest, platform_run_id: str) -> AssertResultManifest:
    input_artifacts = _input_artifacts(request)
    missing_artifacts = [artifact for artifact in input_artifacts if artifact.readiness == 'missing']
    status = 'needs_review' if missing_artifacts else 'pass'
    failures = [
        AssertFailureItem(
            code='missing-input-artifact',
            category='evidence',
            severity='warning',
            summary=f'Input artifact {artifact.artifact_id} is marked missing.',
            evidence_artifact_ids=[artifact.artifact_id],
        )
        for artifact in missing_artifacts
    ]

    manifest_uri = f'local-artifact://assert-v2-sidecar/runs/{platform_run_id}/manifest.json'
    return AssertResultManifest(
        verdict=AssertVerdict(
            status=status,
            score=100.0 if status == 'pass' else None,
            summary='Local synthetic ASSERT sidecar accepted the run evidence for platform ingestion.',
            metrics={
                'input_artifact_count': len(input_artifacts),
                'missing_input_artifact_count': len(missing_artifacts),
            },
        ),
        failures=failures,
        artifacts=[
            {
                'artifact_id': 'local-sidecar-result-manifest',
                'kind': 'manifest',
                'role': 'output',
                'uri': manifest_uri,
                'mime_type': 'application/json',
                'source': 'conversation-agent-evals-local-sidecar',
                'metadata': {'platform_run_id': platform_run_id},
            }
        ],
        summary_artifacts=[
            {
                'artifact_id': 'local-sidecar-ingest-summary',
                'kind': 'summary',
                'role': 'derived',
                'inline_data': {
                    'platform_run_id': platform_run_id,
                    'spec_id': request.spec_ref.spec_id,
                    'spec_kind': request.spec_ref.spec_kind,
                    'verdict': status,
                    'local_only': True,
                },
                'source': 'conversation-agent-evals-local-sidecar',
            }
        ],
        manifest_metadata={
            'assert_version': 'local-synthetic-sidecar',
            'platform_adapter_version': SIDECAR_ADAPTER_VERSION,
            'artifact_manifest_location': manifest_uri,
            'local_only': True,
        },
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


def _platform_run_id(request: AssertRunCreateRequest) -> str:
    fingerprint = json.dumps(request.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]


def _persist_sidecar_record(record: PlatformRunRecord, *, artifact_root: Path | None = None) -> None:
    path = local_assert_sidecar_artifact_path(record.platform_run_id, artifact_root=artifact_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'schema_version': 1,
        'generated_at': datetime.now(UTC).isoformat(),
        'local_only': True,
        'record': record.model_dump(mode='json'),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
