from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.entities import BenchmarkSuiteRunRecord
from app.services.benchmark_service import DETERMINISTIC_EVALUATOR_VERSION, get_suite
from app.services.benchmark_run_store import DEFAULT_PROJECT_ID, DEFAULT_RETENTION_DAYS, DEFAULT_USER_ID

TERMINAL_SUITE_STATUSES = {'completed', 'needs_review', 'failed'}


def create_benchmark_suite_run_record(
    db: Session,
    *,
    suite_run_id: str,
    suite_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    clean_metadata = metadata if isinstance(metadata, dict) else {}
    user_id = _first_text(clean_metadata.get('user_id'), clean_metadata.get('owner_user_id')) or DEFAULT_USER_ID
    project_key = _first_text(clean_metadata.get('project_id'), clean_metadata.get('project_key')) or DEFAULT_PROJECT_ID
    retained_until = _retained_until(clean_metadata, now)

    record = db.get(BenchmarkSuiteRunRecord, suite_run_id)
    if record is None:
        record = BenchmarkSuiteRunRecord(id=suite_run_id, created_at=now)
    record.user_id = user_id
    record.project_key = project_key
    record.suite_id = _required_str(suite_id, 'suite_id')
    record.status = 'queued'
    record.scenario_count = _queued_scenario_count(suite_id)
    record.pass_count = 0
    record.needs_review_count = 0
    record.average_score = 0
    record.report_json = json.dumps(
        _retention_envelope(
            suite_report={
                'suite_run_id': suite_run_id,
                'suite_id': suite_id,
                'run_metadata': clean_metadata,
                'run_lifecycle': _suite_lifecycle(status='queued', now=now, reason='suite run accepted'),
            },
            retained_until=retained_until,
            now=now,
        )
    )
    record.updated_at = now
    record.completed_at = None
    record.retained_until = retained_until
    db.add(record)
    db.commit()
    db.refresh(record)
    return serialize_benchmark_suite_run(record)


def mark_benchmark_suite_run_running(db: Session, *, suite_run_id: str) -> dict[str, Any] | None:
    return _transition_benchmark_suite_run(db, suite_run_id=suite_run_id, status='running', reason='suite execution started')


def mark_benchmark_suite_run_failed(db: Session, *, suite_run_id: str, error: str) -> dict[str, Any] | None:
    return _transition_benchmark_suite_run(db, suite_run_id=suite_run_id, status='failed', reason=error)


def persist_benchmark_suite_run(db: Session, suite_report: dict[str, Any]) -> dict[str, Any]:
    suite_run_id = _required_str(suite_report.get('suite_run_id'), 'suite_run_id')
    now = datetime.now(UTC)
    metadata = suite_report.get('run_metadata') if isinstance(suite_report.get('run_metadata'), dict) else {}
    user_id = _first_text(metadata.get('user_id'), metadata.get('owner_user_id'), suite_report.get('user_id')) or DEFAULT_USER_ID
    project_key = _first_text(metadata.get('project_id'), metadata.get('project_key'), suite_report.get('project_id')) or DEFAULT_PROJECT_ID
    retained_until = _retained_until(metadata, now)

    record = db.get(BenchmarkSuiteRunRecord, suite_run_id)
    if record is None:
        record = BenchmarkSuiteRunRecord(id=suite_run_id, created_at=now)
    record.user_id = user_id
    record.project_key = project_key
    record.suite_id = _required_str(suite_report.get('suite_id'), 'suite_id')
    verdict = _required_str(suite_report.get('verdict'), 'verdict')
    terminal_status = 'completed' if verdict == 'pass' else 'needs_review'
    record.status = terminal_status
    record.scenario_count = _non_negative_int(suite_report.get('scenario_count'))
    record.pass_count = _non_negative_int(suite_report.get('pass_count'))
    record.needs_review_count = _non_negative_int(suite_report.get('needs_review_count'))
    record.average_score = _non_negative_int(suite_report.get('average_score'))
    report_with_lifecycle = dict(suite_report)
    report_with_lifecycle['run_lifecycle'] = _merged_terminal_lifecycle(
        existing_report=_load_json(record.report_json),
        terminal_status=terminal_status,
        now=now,
        reason='suite evaluation completed',
    )
    record.report_json = json.dumps(_retention_envelope(suite_report=report_with_lifecycle, retained_until=retained_until, now=now))
    record.updated_at = now
    record.completed_at = now
    record.retained_until = retained_until
    db.add(record)
    db.commit()
    db.refresh(record)
    return serialize_benchmark_suite_run(record)


def list_benchmark_suite_runs(
    db: Session,
    *,
    user_id: str,
    project_id: str | None = None,
    suite_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    query = db.query(BenchmarkSuiteRunRecord).filter(BenchmarkSuiteRunRecord.user_id == user_id)
    if project_id is not None:
        query = query.filter(BenchmarkSuiteRunRecord.project_key == project_id)
    if suite_id is not None:
        query = query.filter(BenchmarkSuiteRunRecord.suite_id == suite_id)
    if status is not None:
        query = query.filter(BenchmarkSuiteRunRecord.status == status)
    records = query.order_by(BenchmarkSuiteRunRecord.updated_at.desc(), BenchmarkSuiteRunRecord.created_at.desc()).all()
    return [serialize_benchmark_suite_run(record) for record in records]


def get_benchmark_suite_run(db: Session, *, user_id: str, suite_run_id: str) -> dict[str, Any] | None:
    record = (
        db.query(BenchmarkSuiteRunRecord)
        .filter(BenchmarkSuiteRunRecord.id == suite_run_id, BenchmarkSuiteRunRecord.user_id == user_id)
        .first()
    )
    return serialize_benchmark_suite_run(record) if record is not None else None


def export_benchmark_suite_run_vcon_bundle(db: Session, *, user_id: str, suite_run_id: str) -> dict[str, Any] | None:
    record = get_benchmark_suite_run(db=db, user_id=user_id, suite_run_id=suite_run_id)
    if record is None:
        return None

    suite_report = record.get('suite_report') if isinstance(record.get('suite_report'), dict) else {}
    records = _suite_vcon_records(suite_report)
    return {
        'id': suite_run_id,
        'suite_run_id': suite_run_id,
        'suite_id': record.get('suite_id'),
        'suite_name': suite_report.get('suite_name'),
        'user_id': record.get('user_id'),
        'project_id': record.get('project_id'),
        'filename': _suite_vcon_bundle_filename(record),
        'record_count': len(records),
        'records': records,
        'exported_at': _isoformat(datetime.now(UTC)),
    }


def reset_benchmark_suite_run_records_for_tests() -> None:
    with SessionLocal() as db:
        db.query(BenchmarkSuiteRunRecord).delete()
        db.commit()


def serialize_benchmark_suite_run(record: BenchmarkSuiteRunRecord) -> dict[str, Any]:
    retained_report = _load_json(record.report_json)
    suite_report = retained_report.get('suite_report') if isinstance(retained_report.get('suite_report'), dict) else retained_report
    retention = retained_report.get('retention') if isinstance(retained_report.get('retention'), dict) else {}
    scenario_summaries = _scenario_summaries(suite_report)
    return {
        'id': record.id,
        'suite_run_id': record.id,
        'user_id': record.user_id,
        'project_id': record.project_key,
        'suite_id': record.suite_id,
        'status': record.status,
        'scenario_count': record.scenario_count,
        'pass_count': record.pass_count,
        'needs_review_count': record.needs_review_count,
        'average_score': record.average_score,
        'suite_report': suite_report,
        'run_lifecycle': suite_report.get('run_lifecycle') if isinstance(suite_report.get('run_lifecycle'), dict) else {},
        'reliability_metrics': suite_report.get('reliability_metrics') if isinstance(suite_report.get('reliability_metrics'), dict) else {},
        'artifacts': {
            'scenario_summaries': scenario_summaries,
            'vcon_export': _vcon_export_summary(suite_report),
        },
        'retention': {
            'retention_days': retention.get('retention_days', DEFAULT_RETENTION_DAYS),
            'retained_until': _isoformat(record.retained_until),
            'policy': retention.get('policy', 'benchmark_suite_run_report_v1'),
            'evaluator_version': retention.get('evaluator_version', DETERMINISTIC_EVALUATOR_VERSION),
        },
        'created_at': _isoformat(record.created_at),
        'updated_at': _isoformat(record.updated_at),
        'completed_at': _isoformat(record.completed_at),
    }


def _queued_scenario_count(suite_id: str) -> int:
    suite = get_suite(suite_id)
    scenarios = suite.get('scenarios') if isinstance(suite, dict) else None
    return len(scenarios) if isinstance(scenarios, list) else 0


def _retention_envelope(*, suite_report: dict[str, Any], retained_until: datetime, now: datetime) -> dict[str, Any]:
    return {
        'suite_report': suite_report,
        'retention': {
            'policy': 'benchmark_suite_run_report_v1',
            'retention_days': max((retained_until - now).days, 0),
            'retained_until': _isoformat(retained_until),
            'evaluator_version': DETERMINISTIC_EVALUATOR_VERSION,
        },
    }


def _suite_vcon_records(suite_report: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    suite_vcon = suite_report.get('vcon_export')
    if isinstance(suite_vcon, dict):
        records.append(suite_vcon)

    scenario_containers = suite_report.get('scenario_runs') or suite_report.get('scenario_reports') or []
    if not isinstance(scenario_containers, list):
        return records

    for item in scenario_containers:
        if not isinstance(item, dict):
            continue
        report = item.get('benchmark_report') if isinstance(item.get('benchmark_report'), dict) else item
        vcon_export = report.get('vcon_export') if isinstance(report, dict) else None
        if isinstance(vcon_export, dict):
            records.append(vcon_export)
    return records


def _suite_vcon_bundle_filename(record: dict[str, Any]) -> str:
    parts = ['agentbench', record.get('suite_id'), record.get('suite_run_id'), 'vcon-bundle']
    slug = '-'.join(part for part in (_slug_part(part) for part in parts) if part)
    return f'{slug or "agentbench-suite-vcon-bundle"}.json'


def _slug_part(value: Any) -> str:
    import re

    cleaned = re.sub(r'[^a-z0-9-]+', '-', str(value).lower()).strip('-') if value is not None else ''
    return cleaned


def _transition_benchmark_suite_run(db: Session, *, suite_run_id: str, status: str, reason: str) -> dict[str, Any] | None:
    record = db.get(BenchmarkSuiteRunRecord, suite_run_id)
    if record is None:
        return None
    now = datetime.now(UTC)
    retained_report = _load_json(record.report_json)
    suite_report = retained_report.get('suite_report') if isinstance(retained_report.get('suite_report'), dict) else retained_report
    retention = retained_report.get('retention') if isinstance(retained_report.get('retention'), dict) else {}
    retained_until = record.retained_until or _retained_until({}, now)
    record.status = status
    record.updated_at = now
    if status in TERMINAL_SUITE_STATUSES:
        record.completed_at = now
    updated_report = dict(suite_report)
    updated_report['run_lifecycle'] = _append_lifecycle_transition(
        lifecycle=suite_report.get('run_lifecycle') if isinstance(suite_report.get('run_lifecycle'), dict) else {},
        status=status,
        now=now,
        reason=reason,
    )
    if status == 'failed':
        updated_report['error'] = reason
    record.report_json = json.dumps(
        {
            'suite_report': updated_report,
            'retention': {
                'policy': retention.get('policy', 'benchmark_suite_run_report_v1'),
                'retention_days': retention.get('retention_days', DEFAULT_RETENTION_DAYS),
                'retained_until': _isoformat(retained_until),
                'evaluator_version': retention.get('evaluator_version', DETERMINISTIC_EVALUATOR_VERSION),
            },
        }
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return serialize_benchmark_suite_run(record)


def _merged_terminal_lifecycle(
    *,
    existing_report: dict[str, Any],
    terminal_status: str,
    now: datetime,
    reason: str,
) -> dict[str, Any]:
    suite_report = existing_report.get('suite_report') if isinstance(existing_report.get('suite_report'), dict) else existing_report
    lifecycle = suite_report.get('run_lifecycle') if isinstance(suite_report.get('run_lifecycle'), dict) else {}
    if not lifecycle:
        lifecycle = _suite_lifecycle(status='queued', now=now, reason='suite run accepted')
        lifecycle = _append_lifecycle_transition(lifecycle=lifecycle, status='running', now=now, reason='suite execution started')
    return _append_lifecycle_transition(lifecycle=lifecycle, status=terminal_status, now=now, reason=reason)


def _suite_lifecycle(*, status: str, now: datetime, reason: str) -> dict[str, Any]:
    return {
        'status': status,
        'terminal': status in TERMINAL_SUITE_STATUSES,
        'queued_at': _isoformat(now) if status == 'queued' else None,
        'started_at': _isoformat(now) if status == 'running' else None,
        'completed_at': _isoformat(now) if status in TERMINAL_SUITE_STATUSES else None,
        'transitions': [{'from': None, 'to': status, 'at': _isoformat(now), 'reason': reason}],
    }


def _append_lifecycle_transition(*, lifecycle: dict[str, Any], status: str, now: datetime, reason: str) -> dict[str, Any]:
    previous_status = lifecycle.get('status') if isinstance(lifecycle.get('status'), str) else None
    transitions = lifecycle.get('transitions') if isinstance(lifecycle.get('transitions'), list) else []
    updated = dict(lifecycle)
    if previous_status != status:
        updated['transitions'] = [*transitions, {'from': previous_status, 'to': status, 'at': _isoformat(now), 'reason': reason}]
    else:
        updated['transitions'] = transitions
    updated['status'] = status
    updated['terminal'] = status in TERMINAL_SUITE_STATUSES
    if status == 'queued' and not updated.get('queued_at'):
        updated['queued_at'] = _isoformat(now)
    if status == 'running' and not updated.get('started_at'):
        updated['started_at'] = _isoformat(now)
    if status in TERMINAL_SUITE_STATUSES:
        updated['completed_at'] = _isoformat(now)
    return updated


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


def _non_negative_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _required_str(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f'benchmark suite report missing {field_name}')


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


def _scenario_summaries(suite_report: dict[str, Any]) -> list[dict[str, Any]]:
    runs = suite_report.get('scenario_runs') if isinstance(suite_report.get('scenario_runs'), list) else None
    if runs is None:
        runs = suite_report.get('scenario_reports') if isinstance(suite_report.get('scenario_reports'), list) else []

    summaries: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        report = run.get('benchmark_report') if isinstance(run.get('benchmark_report'), dict) else run
        if not isinstance(report, dict):
            continue
        summaries.append(
            {
                'suite_id': report.get('suite_id'),
                'scenario_id': report.get('scenario_id'),
                'run_id': report.get('run_id'),
                'status': report.get('run_status') or report.get('verdict'),
                'overall_score': report.get('overall_score'),
                'failure_categories': report.get('failure_categories') if isinstance(report.get('failure_categories'), list) else [],
            }
        )
    return summaries


def _vcon_export_summary(suite_report: dict[str, Any]) -> dict[str, Any]:
    vcon_export = suite_report.get('vcon_export') if isinstance(suite_report.get('vcon_export'), dict) else {}
    analysis = vcon_export.get('analysis') if isinstance(vcon_export.get('analysis'), list) else []
    dialog = vcon_export.get('dialog') if isinstance(vcon_export.get('dialog'), list) else []
    return {
        'available': bool(vcon_export),
        'dialog_turns': len(dialog),
        'analysis_count': len(analysis),
        'source_format': vcon_export.get('source_format'),
        'appended_analysis_type': analysis[-1].get('type') if analysis and isinstance(analysis[-1], dict) else None,
    }
