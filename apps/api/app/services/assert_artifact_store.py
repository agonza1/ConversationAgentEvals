from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ARTIFACT_STORE_VERSION = 'assert-artifact-store-v1'
ARTIFACT_ROOT = Path(__file__).resolve().parents[2] / 'artifacts' / 'assert-v2'


def persist_assert_run_artifacts(report: dict[str, Any]) -> dict[str, Any]:
    run_id = _required_text(report.get('run_id'), 'run_id')
    manifest_location = _manifest_location(run_id)
    manifest_path = _manifest_path(run_id)
    now = _isoformat(datetime.now(UTC))

    canonical_manifest = canonical_assert_run_manifest(
        report,
        manifest_location=manifest_location,
        created_at=now,
    )
    encoded = _stable_json(canonical_manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(encoded, encoding='utf-8')

    pointer = {
        'artifact_id': 'assert-canonical-run-manifest',
        'kind': 'manifest',
        'role': 'output',
        'uri': manifest_location,
        'mime_type': 'application/json',
        'sha256': hashlib.sha256(encoded.encode('utf-8')).hexdigest(),
        'size_bytes': len(encoded.encode('utf-8')),
        'source': 'conversation-agent-evals',
        'metadata': {'store_version': ARTIFACT_STORE_VERSION},
    }
    return {
        'manifest_location': manifest_location,
        'manifest_path': str(manifest_path),
        'manifest': canonical_manifest,
        'pointer': pointer,
    }


def load_assert_run_artifact_manifest(location: str) -> dict[str, Any] | None:
    path = _path_from_location(location)
    if path is None or not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def canonical_assert_run_manifest(
    report: dict[str, Any],
    *,
    manifest_location: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    manifest = report.get('assert_result_manifest') if isinstance(report.get('assert_result_manifest'), dict) else {}
    platform_record = report.get('assert_platform_record') if isinstance(report.get('assert_platform_record'), dict) else {}
    return {
        'store_version': ARTIFACT_STORE_VERSION,
        'run_id': report.get('run_id'),
        'logical_run_id': report.get('logical_run_id'),
        'assert_run_id': report.get('assert_run_id'),
        'suite_id': report.get('suite_id'),
        'scenario_id': report.get('scenario_id'),
        'created_at': created_at,
        'manifest_location': manifest_location,
        'assert_result_manifest': _with_manifest_location(manifest, manifest_location),
        'assert_platform_record': _with_platform_manifest_location(platform_record, manifest_location),
        'platform_metadata_index': platform_metadata_index(report, manifest_location=manifest_location),
    }


def platform_metadata_index(report: dict[str, Any], *, manifest_location: str | None = None) -> dict[str, Any]:
    assert_manifest = report.get('assert_result_manifest') if isinstance(report.get('assert_result_manifest'), dict) else {}
    manifest_metadata = assert_manifest.get('manifest_metadata') if isinstance(assert_manifest.get('manifest_metadata'), dict) else {}
    lifecycle = report.get('run_lifecycle') if isinstance(report.get('run_lifecycle'), dict) else {}
    run_metadata = report.get('run_metadata') if isinstance(report.get('run_metadata'), dict) else {}
    return {
        'run_id': report.get('run_id'),
        'logical_run_id': report.get('logical_run_id'),
        'assert_run_id': report.get('assert_run_id'),
        'suite_id': report.get('suite_id'),
        'scenario_id': report.get('scenario_id'),
        'status': report.get('run_status') or lifecycle.get('status') or report.get('verdict'),
        'verdict': report.get('verdict'),
        'score': report.get('overall_score', report.get('score')),
        'user_id': run_metadata.get('user_id') or report.get('user_id'),
        'project_id': run_metadata.get('project_id') or report.get('project_id'),
        'attempt': lifecycle.get('attempt'),
        'retryable': lifecycle.get('retryable'),
        'retry_parent_run_id': lifecycle.get('retry_of_run_id'),
        'resume_parent_run_id': lifecycle.get('resume_from_run_id'),
        'assert_version': manifest_metadata.get('assert_version'),
        'assert_commit': manifest_metadata.get('assert_commit'),
        'adapter_version': manifest_metadata.get('platform_adapter_version'),
        'spec_version': manifest_metadata.get('spec_version'),
        'provider_model_settings': manifest_metadata.get('provider_model_settings') or {},
        'artifact_manifest_location': manifest_location or manifest_metadata.get('artifact_manifest_location'),
        'platform_version': manifest_metadata.get('platform_version'),
        'updated_at': lifecycle.get('updated_at'),
        'completed_at': lifecycle.get('completed_at') or lifecycle.get('needs_review_at'),
    }


def platform_report_index(report: dict[str, Any]) -> dict[str, Any]:
    indexed = deepcopy(report)
    indexed.pop('assert_result_manifest', None)
    indexed.pop('assert_platform_record', None)
    canonical = indexed.get('assert_canonical_artifact') if isinstance(indexed.get('assert_canonical_artifact'), dict) else {}
    location = canonical.get('uri') if isinstance(canonical.get('uri'), str) else None
    indexed['assert_platform_metadata_index'] = platform_metadata_index(report, manifest_location=location)
    indexed['assert_canonical_artifact'] = canonical
    indexed['assert_canonical_manifest'] = _durable_canonical_manifest(report, manifest_location=location)
    return indexed


def _durable_canonical_manifest(report: dict[str, Any], *, manifest_location: str | None) -> dict[str, Any]:
    existing = report.get('assert_canonical_manifest')
    if isinstance(existing, dict):
        return deepcopy(existing)

    if manifest_location:
        loaded = load_assert_run_artifact_manifest(manifest_location)
        if loaded is not None:
            return loaded

    has_assert_payload = isinstance(report.get('assert_result_manifest'), dict) or isinstance(report.get('assert_platform_record'), dict)
    if not has_assert_payload:
        return {}

    return canonical_assert_run_manifest(report, manifest_location=manifest_location or '')


def _with_manifest_location(manifest: dict[str, Any], manifest_location: str) -> dict[str, Any]:
    updated = deepcopy(manifest)
    metadata = updated.get('manifest_metadata') if isinstance(updated.get('manifest_metadata'), dict) else {}
    updated['manifest_metadata'] = {**metadata, 'artifact_manifest_location': manifest_location}
    return updated


def _with_platform_manifest_location(platform_record: dict[str, Any], manifest_location: str) -> dict[str, Any]:
    updated = deepcopy(platform_record)
    summary = updated.get('summary') if isinstance(updated.get('summary'), dict) else {}
    updated['summary'] = {**summary, 'artifact_manifest_location': manifest_location}
    return updated


def _manifest_location(run_id: str) -> str:
    return f'local-artifact://assert-v2/runs/{run_id}/manifest.json'


def _manifest_path(run_id: str) -> Path:
    return ARTIFACT_ROOT / 'runs' / run_id / 'manifest.json'


def _path_from_location(location: str) -> Path | None:
    prefix = 'local-artifact://assert-v2/'
    if not isinstance(location, str) or not location.startswith(prefix):
        return None
    relative = location.removeprefix(prefix)
    return ARTIFACT_ROOT / relative


def _required_text(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f'assert artifact manifest missing {field_name}')


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def _isoformat(value: datetime) -> str:
    return value.isoformat().replace('+00:00', 'Z')
