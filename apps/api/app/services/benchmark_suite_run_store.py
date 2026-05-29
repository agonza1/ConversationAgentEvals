from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.entities import BenchmarkSuiteRunRecord
from app.services.benchmark_service import DETERMINISTIC_EVALUATOR_VERSION
from app.services.benchmark_run_store import DEFAULT_PROJECT_ID, DEFAULT_RETENTION_DAYS, DEFAULT_USER_ID


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
    record.status = 'completed' if verdict == 'pass' else 'needs_review'
    record.scenario_count = _non_negative_int(suite_report.get('scenario_count'))
    record.pass_count = _non_negative_int(suite_report.get('pass_count'))
    record.needs_review_count = _non_negative_int(suite_report.get('needs_review_count'))
    record.average_score = _non_negative_int(suite_report.get('average_score'))
    record.report_json = json.dumps(_retention_envelope(suite_report=suite_report, retained_until=retained_until, now=now))
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


def reset_benchmark_suite_run_records_for_tests() -> None:
    with SessionLocal() as db:
        db.query(BenchmarkSuiteRunRecord).delete()
        db.commit()


def serialize_benchmark_suite_run(record: BenchmarkSuiteRunRecord) -> dict[str, Any]:
    retained_report = _load_json(record.report_json)
    suite_report = retained_report.get('suite_report') if isinstance(retained_report.get('suite_report'), dict) else retained_report
    retention = retained_report.get('retention') if isinstance(retained_report.get('retention'), dict) else {}
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
