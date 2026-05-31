from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.entities import BenchmarkRunRecord
from app.services.benchmark_service import DETERMINISTIC_EVALUATOR_VERSION


DEFAULT_RETENTION_DAYS = 90
DEFAULT_USER_ID = 'anonymous'
DEFAULT_PROJECT_ID = 'default'


def persist_benchmark_run(db: Session, report: dict[str, Any], transcript: str | None = None) -> dict[str, Any]:
    run_id = _required_str(report.get('run_id'), 'run_id')
    now = datetime.now(UTC)
    lifecycle = report.get('run_lifecycle') if isinstance(report.get('run_lifecycle'), dict) else {}
    metadata = report.get('run_metadata') if isinstance(report.get('run_metadata'), dict) else {}
    user_id = _first_text(metadata.get('user_id'), metadata.get('owner_user_id'), report.get('user_id')) or DEFAULT_USER_ID
    project_key = _first_text(metadata.get('project_id'), metadata.get('project_key'), report.get('project_id')) or DEFAULT_PROJECT_ID
    retained_until = _retained_until(metadata, now)
    completed_at = _parse_datetime(lifecycle.get('completed_at') or lifecycle.get('needs_review_at'))

    record = db.get(BenchmarkRunRecord, run_id)
    if record is None:
        record = BenchmarkRunRecord(id=run_id, created_at=_parse_datetime(lifecycle.get('started_at')) or now)
    record.user_id = user_id
    record.project_key = project_key
    record.suite_id = _required_str(report.get('suite_id'), 'suite_id')
    record.scenario_id = _required_str(report.get('scenario_id'), 'scenario_id')
    record.logical_run_id = _required_str(report.get('logical_run_id'), 'logical_run_id')
    record.status = _required_str(report.get('run_status') or lifecycle.get('status') or report.get('verdict'), 'run_status')
    record.attempt = _positive_int(lifecycle.get('attempt'), default=1)
    record.report_json = json.dumps(_retention_envelope(report=report, retained_until=retained_until, now=now))
    record.transcript = transcript if transcript is not None else report.get('transcript_preview')
    record.updated_at = now
    record.completed_at = completed_at
    record.retained_until = retained_until
    db.add(record)
    db.commit()
    db.refresh(record)
    return serialize_benchmark_run(record)


def list_benchmark_runs(
    db: Session,
    *,
    user_id: str,
    project_id: str | None = None,
    suite_id: str | None = None,
    scenario_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = db.query(BenchmarkRunRecord).filter(BenchmarkRunRecord.user_id == user_id)
    if project_id is not None:
        query = query.filter(BenchmarkRunRecord.project_key == project_id)
    if suite_id is not None:
        query = query.filter(BenchmarkRunRecord.suite_id == suite_id)
    if scenario_id is not None:
        query = query.filter(BenchmarkRunRecord.scenario_id == scenario_id)
    if status is not None:
        query = query.filter(BenchmarkRunRecord.status == status)
    records = query.order_by(BenchmarkRunRecord.updated_at.desc(), BenchmarkRunRecord.created_at.desc()).all()
    return [serialize_benchmark_run(record) for record in records]


def get_benchmark_run(db: Session, *, user_id: str, run_id: str) -> dict[str, Any] | None:
    record = db.query(BenchmarkRunRecord).filter(BenchmarkRunRecord.id == run_id, BenchmarkRunRecord.user_id == user_id).first()
    return serialize_benchmark_run(record) if record is not None else None


def export_benchmark_run_vcon(db: Session, *, user_id: str, run_id: str) -> dict[str, Any] | None:
    record = get_benchmark_run(db=db, user_id=user_id, run_id=run_id)
    if record is None:
        return None

    report = record.get('report') if isinstance(record.get('report'), dict) else {}
    vcon_export = report.get('vcon_export') if isinstance(report.get('vcon_export'), dict) else None
    if vcon_export is None:
        return None

    return {
        'id': run_id,
        'run_id': run_id,
        'suite_id': record.get('suite_id'),
        'scenario_id': record.get('scenario_id'),
        'user_id': record.get('user_id'),
        'project_id': record.get('project_id'),
        'filename': _run_vcon_filename(record),
        'record': vcon_export,
        'exported_at': _isoformat(datetime.now(UTC)),
    }


def get_benchmark_run_audit_artifacts(db: Session, *, user_id: str, run_id: str) -> dict[str, Any] | None:
    record = get_benchmark_run(db=db, user_id=user_id, run_id=run_id)
    if record is None:
        return None

    report = record.get('report') if isinstance(record.get('report'), dict) else {}
    evidence_artifacts = report.get('evidence_artifacts') if isinstance(report.get('evidence_artifacts'), dict) else {}
    audit_summary = report.get('evidence_audit_summary') if isinstance(report.get('evidence_audit_summary'), dict) else {}
    lifecycle = report.get('run_lifecycle') if isinstance(report.get('run_lifecycle'), dict) else {}
    retention = record.get('retention') if isinstance(record.get('retention'), dict) else {}
    report_json = _stable_json(report)
    export_readiness = audit_summary.get('export_readiness') if isinstance(audit_summary.get('export_readiness'), dict) else {}
    artifacts = evidence_artifacts.get('artifacts') if isinstance(evidence_artifacts.get('artifacts'), list) else []

    return {
        'id': run_id,
        'run_id': run_id,
        'logical_run_id': record.get('logical_run_id'),
        'suite_id': record.get('suite_id'),
        'scenario_id': record.get('scenario_id'),
        'user_id': record.get('user_id'),
        'project_id': record.get('project_id'),
        'status': record.get('status'),
        'attempt': record.get('attempt'),
        'filename': _run_audit_artifacts_filename(record),
        'operator_summary': {
            'verdict': report.get('verdict'),
            'overall_score': report.get('overall_score', report.get('score')),
            'ready_for_export': bool(export_readiness.get('ready')),
            'missing_export_artifacts': export_readiness.get('missing') if isinstance(export_readiness.get('missing'), list) else [],
            'artifact_count': len(artifacts),
            'evaluator_version': audit_summary.get('evaluator_version') or retention.get('evaluator_version'),
        },
        'evidence_fingerprint': evidence_artifacts.get('evidence_fingerprint'),
        'evidence_artifacts': artifacts,
        'audit_summary': audit_summary,
        'run_lifecycle': lifecycle,
        'contract_artifact': {
            'type': 'scenario_contract',
            'suite_id': record.get('suite_id'),
            'scenario_id': record.get('scenario_id'),
            'sha256': report.get('scenario_contract_sha256'),
        },
        'report_artifact': {
            'type': 'deterministic_report',
            'sha256': hashlib.sha256(report_json.encode('utf-8')).hexdigest(),
            'size_bytes': len(report_json.encode('utf-8')),
        },
        'retention': retention,
        'generated_at': _isoformat(datetime.now(UTC)),
    }


def export_benchmark_run_history(
    db: Session,
    *,
    user_id: str,
    project_id: str | None = None,
    suite_id: str | None = None,
    scenario_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    records = list_benchmark_runs(
        db=db,
        user_id=user_id,
        project_id=project_id,
        suite_id=suite_id,
        scenario_id=scenario_id,
        status=status,
    )
    return {
        'id': _history_export_id(user_id=user_id, project_id=project_id, suite_id=suite_id, scenario_id=scenario_id),
        'user_id': user_id,
        'project_id': project_id,
        'suite_id': suite_id,
        'scenario_id': scenario_id,
        'status': status,
        'filename': _history_export_filename(project_id=project_id, suite_id=suite_id, scenario_id=scenario_id),
        'run_count': len(records),
        'summary': _history_summary(records),
        'vcon_export_summary': _history_vcon_summary(records),
        'contract_artifact_summary': _history_contract_artifact_summary(records),
        'runs': records,
        'exported_at': _isoformat(datetime.now(UTC)),
    }


def reset_benchmark_run_records_for_tests() -> None:
    with SessionLocal() as db:
        db.query(BenchmarkRunRecord).delete()
        db.commit()


def serialize_benchmark_run(record: BenchmarkRunRecord) -> dict[str, Any]:
    retained_report = _load_json(record.report_json)
    report = retained_report.get('report') if isinstance(retained_report.get('report'), dict) else retained_report
    retention = retained_report.get('retention') if isinstance(retained_report.get('retention'), dict) else {}
    return {
        'id': record.id,
        'run_id': record.id,
        'logical_run_id': record.logical_run_id,
        'user_id': record.user_id,
        'project_id': record.project_key,
        'suite_id': record.suite_id,
        'scenario_id': record.scenario_id,
        'status': record.status,
        'attempt': record.attempt,
        'report': report,
        'transcript': record.transcript,
        'retention': {
            'retention_days': retention.get('retention_days', DEFAULT_RETENTION_DAYS),
            'retained_until': _isoformat(record.retained_until),
            'policy': retention.get('policy', 'benchmark_run_report_v1'),
            'evaluator_version': retention.get('evaluator_version', DETERMINISTIC_EVALUATOR_VERSION),
        },
        'created_at': _isoformat(record.created_at),
        'updated_at': _isoformat(record.updated_at),
        'completed_at': _isoformat(record.completed_at),
    }


def _run_vcon_filename(record: dict[str, Any]) -> str:
    parts = ['agentbench', record.get('suite_id'), record.get('scenario_id'), record.get('run_id'), 'vcon']
    slug = '-'.join(part for part in (_slug_part(part) for part in parts) if part)
    return f'{slug or "agentbench-run-vcon"}.json'


def _run_audit_artifacts_filename(record: dict[str, Any]) -> str:
    parts = ['agentbench', record.get('suite_id'), record.get('scenario_id'), record.get('run_id'), 'audit-artifacts']
    slug = '-'.join(part for part in (_slug_part(part) for part in parts) if part)
    return f'{slug or "agentbench-run-audit-artifacts"}.json'


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def _slug_part(value: Any) -> str:
    import re

    cleaned = re.sub(r'[^a-z0-9-]+', '-', str(value).lower()).strip('-') if value is not None else ''
    return cleaned


def _history_export_id(*, user_id: str, project_id: str | None, suite_id: str | None, scenario_id: str | None) -> str:
    parts = ['benchmark-history', user_id, project_id, suite_id, scenario_id]
    return '-'.join(part for part in (_slug_part(part) for part in parts) if part)


def _history_export_filename(*, project_id: str | None, suite_id: str | None, scenario_id: str | None) -> str:
    parts = ['agentbench', project_id, suite_id, scenario_id, 'benchmark-history']
    slug = '-'.join(part for part in (_slug_part(part) for part in parts) if part)
    return f'{slug or "agentbench-benchmark-history"}.json'


def _history_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [_score_from_record(record) for record in records]
    numeric_scores = [score for score in scores if score is not None]
    latest_score = scores[0] if scores else None
    previous_score = next((score for score in scores[1:] if score is not None), None)
    latest_delta = latest_score - previous_score if latest_score is not None and previous_score is not None else None
    status_counts: dict[str, int] = {}
    for record in records:
        status = str(record.get('status') or 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        'latest_run_id': records[0].get('run_id') if records else None,
        'latest_status': records[0].get('status') if records else None,
        'latest_score': latest_score,
        'previous_score': previous_score,
        'latest_delta': latest_delta,
        'latest_trend': _score_trend(latest_score, previous_score),
        'best_score': max(numeric_scores) if numeric_scores else None,
        'worst_score': min(numeric_scores) if numeric_scores else None,
        'average_score': round(sum(numeric_scores) / len(numeric_scores), 2) if numeric_scores else None,
        'status_counts': status_counts,
    }


def _history_vcon_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    available_records = 0
    dialog_turns = 0
    analysis_records = 0
    for record in records:
        report = record.get('report') if isinstance(record.get('report'), dict) else {}
        vcon_export = report.get('vcon_export') if isinstance(report.get('vcon_export'), dict) else None
        if not vcon_export:
            continue
        available_records += 1
        dialog = vcon_export.get('dialog') if isinstance(vcon_export.get('dialog'), list) else []
        analysis = vcon_export.get('analysis') if isinstance(vcon_export.get('analysis'), list) else []
        dialog_turns += len(dialog)
        analysis_records += len(analysis)
    return {
        'available_records': available_records,
        'missing_records': max(len(records) - available_records, 0),
        'total_runs': len(records),
        'dialog_turns': dialog_turns,
        'analysis_records': analysis_records,
    }


def _history_contract_artifact_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    available_records = 0
    suite_hashes: set[str] = set()
    scenario_hashes: set[str] = set()
    for record in records:
        report = record.get('report') if isinstance(record.get('report'), dict) else {}
        suite_hash = report.get('suite_contract_manifest_sha256')
        scenario_hash = report.get('scenario_contract_sha256')
        has_contract_artifact = isinstance(suite_hash, str) and bool(suite_hash) and isinstance(scenario_hash, str) and bool(scenario_hash)
        if not has_contract_artifact:
            continue
        available_records += 1
        suite_hashes.add(suite_hash)
        scenario_hashes.add(scenario_hash)

    return {
        'available_records': available_records,
        'missing_records': max(len(records) - available_records, 0),
        'total_runs': len(records),
        'suite_contract_manifest_sha256s': sorted(suite_hashes),
        'scenario_contract_sha256s': sorted(scenario_hashes),
    }


def _score_from_record(record: dict[str, Any]) -> int | float | None:
    report = record.get('report') if isinstance(record.get('report'), dict) else {}
    score = report.get('overall_score', report.get('score'))
    return score if isinstance(score, (int, float)) and not isinstance(score, bool) else None


def _score_trend(latest_score: int | float | None, previous_score: int | float | None) -> str:
    if latest_score is None:
        return 'unscored'
    if previous_score is None:
        return 'baseline'
    if latest_score > previous_score:
        return 'improved'
    if latest_score < previous_score:
        return 'regressed'
    return 'unchanged'


def _retention_envelope(*, report: dict[str, Any], retained_until: datetime, now: datetime) -> dict[str, Any]:
    return {
        'report': report,
        'retention': {
            'policy': 'benchmark_run_report_v1',
            'retention_days': max((retained_until - now).days, 0),
            'retained_until': _isoformat(retained_until),
            'evaluator_version': DETERMINISTIC_EVALUATOR_VERSION,
        },
    }


def _retained_until(metadata: dict[str, Any], now: datetime) -> datetime:
    retention_days = _positive_int(metadata.get('retention_days'), default=DEFAULT_RETENTION_DAYS)
    return now + timedelta(days=retention_days)


def _positive_int(value: Any, *, default: int) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _required_str(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f'benchmark report missing {field_name}')


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _load_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
