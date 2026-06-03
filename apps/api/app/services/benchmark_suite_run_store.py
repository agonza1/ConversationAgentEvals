from __future__ import annotations

import json
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.entities import BenchmarkSuiteRunRecord
from app.services.benchmark_service import DETERMINISTIC_EVALUATOR_VERSION, get_suite, get_suite_contract_manifest
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
    suite_contract_manifest_sha256 = _suite_contract_manifest_sha256(suite_id)
    record.report_json = json.dumps(
        _retention_envelope(
            suite_report={
                'suite_run_id': suite_run_id,
                'suite_id': suite_id,
                'suite_contract_manifest_sha256': suite_contract_manifest_sha256,
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


def get_benchmark_suite_run_audit_artifacts(db: Session, *, user_id: str, suite_run_id: str) -> dict[str, Any] | None:
    record = get_benchmark_suite_run(db=db, user_id=user_id, suite_run_id=suite_run_id)
    if record is None:
        return None

    suite_report = record.get('suite_report') if isinstance(record.get('suite_report'), dict) else {}
    scenario_artifacts = _suite_scenario_audit_artifacts(suite_report)
    ready_scenarios = [artifact for artifact in scenario_artifacts if artifact.get('ready_for_export')]
    missing_scenarios = [artifact for artifact in scenario_artifacts if not artifact.get('ready_for_export')]
    report_json = _stable_json(suite_report)
    lifecycle = record.get('run_lifecycle') if isinstance(record.get('run_lifecycle'), dict) else {}
    retention = record.get('retention') if isinstance(record.get('retention'), dict) else {}

    return {
        'id': suite_run_id,
        'suite_run_id': suite_run_id,
        'suite_id': record.get('suite_id'),
        'suite_name': suite_report.get('suite_name'),
        'user_id': record.get('user_id'),
        'project_id': record.get('project_id'),
        'status': record.get('status'),
        'filename': _suite_audit_artifacts_filename(record),
        'operator_summary': {
            'verdict': suite_report.get('verdict'),
            'average_score': record.get('average_score'),
            'scenario_count': record.get('scenario_count'),
            'ready_scenarios': len(ready_scenarios),
            'missing_scenarios': len(missing_scenarios),
            'ready_for_export': len(scenario_artifacts) > 0 and len(missing_scenarios) == 0,
            'evaluator_version': retention.get('evaluator_version', DETERMINISTIC_EVALUATOR_VERSION),
        },
        'suite_contract_artifact': {
            'type': 'suite_contract_manifest',
            'suite_id': record.get('suite_id'),
            'sha256': record.get('suite_contract_manifest_sha256'),
        },
        'scenario_artifacts': scenario_artifacts,
        'report_artifact': {
            'type': 'deterministic_suite_report',
            'sha256': hashlib.sha256(report_json.encode('utf-8')).hexdigest(),
            'size_bytes': len(report_json.encode('utf-8')),
        },
        'run_lifecycle': lifecycle,
        'retention': retention,
        'generated_at': _isoformat(datetime.now(UTC)),
    }


def export_benchmark_suite_run_history(
    db: Session,
    *,
    user_id: str,
    project_id: str | None = None,
    suite_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    records = list_benchmark_suite_runs(
        db=db,
        user_id=user_id,
        project_id=project_id,
        suite_id=suite_id,
        status=status,
    )
    return {
        'id': _suite_history_export_id(user_id=user_id, project_id=project_id, suite_id=suite_id, status=status),
        'user_id': user_id,
        'project_id': project_id,
        'suite_id': suite_id,
        'status': status,
        'filename': _suite_history_export_filename(project_id=project_id, suite_id=suite_id, status=status),
        'suite_run_count': len(records),
        'summary': _suite_history_summary(records),
        'scenario_coverage_summary': _suite_history_scenario_coverage(records=records, suite_id=suite_id),
        'vcon_export_summary': _suite_history_vcon_summary(records),
        'suite_contract_artifact_summary': _suite_history_contract_artifact_summary(records),
        'suite_runs': records,
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
    progress = _suite_run_progress(
        status=record.status,
        scenario_count=record.scenario_count,
        scenario_summaries=scenario_summaries,
    )
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
        'suite_contract_manifest_sha256': suite_report.get('suite_contract_manifest_sha256') or _suite_contract_manifest_sha256(record.suite_id),
        'suite_report': suite_report,
        'run_lifecycle': suite_report.get('run_lifecycle') if isinstance(suite_report.get('run_lifecycle'), dict) else {},
        'progress': progress,
        'reliability_metrics': suite_report.get('reliability_metrics') if isinstance(suite_report.get('reliability_metrics'), dict) else {},
        'artifacts': {
            'scenario_summaries': scenario_summaries,
            'vcon_export': _vcon_export_summary(suite_report),
            'suite_contract_manifest_sha256': suite_report.get('suite_contract_manifest_sha256') or _suite_contract_manifest_sha256(record.suite_id),
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


def _suite_contract_manifest_sha256(suite_id: str) -> str:
    manifest = get_suite_contract_manifest(suite_id)
    if not isinstance(manifest, dict):
        return ''
    value = manifest.get('suite_contract_manifest_sha256')
    return value if isinstance(value, str) else ''


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


def _suite_audit_artifacts_filename(record: dict[str, Any]) -> str:
    parts = ['agentbench', record.get('suite_id'), record.get('suite_run_id'), 'suite-audit-artifacts']
    slug = '-'.join(part for part in (_slug_part(part) for part in parts) if part)
    return f'{slug or "agentbench-suite-audit-artifacts"}.json'


def _suite_scenario_audit_artifacts(suite_report: dict[str, Any]) -> list[dict[str, Any]]:
    scenario_containers = suite_report.get('scenario_runs') or suite_report.get('scenario_reports') or []
    if not isinstance(scenario_containers, list):
        return []

    artifacts: list[dict[str, Any]] = []
    for item in scenario_containers:
        if not isinstance(item, dict):
            continue
        report = item.get('benchmark_report') if isinstance(item.get('benchmark_report'), dict) else item
        if not isinstance(report, dict):
            continue
        audit_summary = report.get('evidence_audit_summary') if isinstance(report.get('evidence_audit_summary'), dict) else {}
        export_readiness = audit_summary.get('export_readiness') if isinstance(audit_summary.get('export_readiness'), dict) else {}
        evidence_artifacts = report.get('evidence_artifacts') if isinstance(report.get('evidence_artifacts'), dict) else {}
        report_json = _stable_json(report)
        artifacts.append(
            {
                'run_id': report.get('run_id'),
                'logical_run_id': report.get('logical_run_id'),
                'suite_id': report.get('suite_id') or suite_report.get('suite_id'),
                'scenario_id': report.get('scenario_id'),
                'status': report.get('run_status') or report.get('verdict'),
                'verdict': report.get('verdict'),
                'overall_score': report.get('overall_score', report.get('score')),
                'ready_for_export': bool(export_readiness.get('ready')),
                'missing_export_artifacts': export_readiness.get('missing') if isinstance(export_readiness.get('missing'), list) else [],
                'input_artifact_types': audit_summary.get('input_artifact_types') if isinstance(audit_summary.get('input_artifact_types'), list) else [],
                'evidence_fingerprint': evidence_artifacts.get('evidence_fingerprint'),
                'scenario_contract_sha256': report.get('scenario_contract_sha256'),
                'report_sha256': hashlib.sha256(report_json.encode('utf-8')).hexdigest(),
            }
        )
    return artifacts


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def _suite_history_export_id(*, user_id: str, project_id: str | None, suite_id: str | None, status: str | None) -> str:
    parts = ['suite-history', user_id, project_id, suite_id, status]
    return '-'.join(part for part in (_slug_part(part) for part in parts) if part)


def _suite_history_export_filename(*, project_id: str | None, suite_id: str | None, status: str | None) -> str:
    parts = ['agentbench', project_id, suite_id, status, 'suite-run-history']
    slug = '-'.join(part for part in (_slug_part(part) for part in parts) if part)
    return f'{slug or "agentbench-suite-run-history"}.json'


def _suite_history_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [_suite_score_from_record(record) for record in records]
    numeric_scores = [score for score in scores if score is not None]
    latest_score = scores[0] if scores else None
    previous_score = next((score for score in scores[1:] if score is not None), None)
    latest_delta = latest_score - previous_score if latest_score is not None and previous_score is not None else None
    status_counts: dict[str, int] = {}
    failure_category_counts: dict[str, int] = {}
    total_scenarios = 0
    total_passes = 0
    total_needs_review = 0
    active_suite_runs = 0
    terminal_suite_runs = 0
    for record in records:
        status = str(record.get('status') or 'unknown')
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in TERMINAL_SUITE_STATUSES:
            terminal_suite_runs += 1
        else:
            active_suite_runs += 1
        total_scenarios += _non_negative_int(record.get('scenario_count'))
        total_passes += _non_negative_int(record.get('pass_count'))
        total_needs_review += _non_negative_int(record.get('needs_review_count'))
        for category in _suite_failure_categories(record):
            failure_category_counts[category] = failure_category_counts.get(category, 0) + 1

    return {
        'latest_suite_run_id': records[0].get('suite_run_id') if records else None,
        'latest_status': records[0].get('status') if records else None,
        'latest_average_score': latest_score,
        'previous_average_score': previous_score,
        'latest_delta': latest_delta,
        'latest_trend': _score_trend(latest_score, previous_score),
        'best_average_score': max(numeric_scores) if numeric_scores else None,
        'worst_average_score': min(numeric_scores) if numeric_scores else None,
        'average_score': round(sum(numeric_scores) / len(numeric_scores), 2) if numeric_scores else None,
        'status_counts': status_counts,
        'active_suite_runs': active_suite_runs,
        'terminal_suite_runs': terminal_suite_runs,
        'total_scenarios': total_scenarios,
        'total_passes': total_passes,
        'total_needs_review': total_needs_review,
        'pass_rate': round((total_passes / total_scenarios) * 100, 2) if total_scenarios else None,
        'failure_category_counts': dict(sorted(failure_category_counts.items())),
        'top_failure_categories': _top_failure_categories(failure_category_counts),
    }


def _suite_history_scenario_coverage(*, records: list[dict[str, Any]], suite_id: str | None) -> dict[str, Any]:
    covered_ids = sorted({
        str(summary.get("scenario_id"))
        for record in records
        for summary in _scenario_summaries(record.get("suite_report") if isinstance(record.get("suite_report"), dict) else {})
        if summary.get("scenario_id")
    })
    if suite_id is None:
        return {
            "suite_id": None,
            "scenario_count": None,
            "covered_scenario_count": len(covered_ids),
            "coverage_percent": None,
            "covered_scenario_ids": covered_ids,
            "missing_scenario_ids": [],
            "covered_scenarios": [{"id": scenario_id, "title": scenario_id} for scenario_id in covered_ids],
            "missing_scenarios": [],
            "recommended_next_scenario": None,
            "coverage_status": "partial" if covered_ids else "empty",
        }

    suite = get_suite(suite_id)
    scenario_titles = {
        str(scenario.get("id")): str(scenario.get("title") or scenario.get("id"))
        for scenario in suite.get("scenarios", [])
        if scenario.get("id")
    } if suite else {}
    scenario_ids = list(scenario_titles.keys())
    if suite_id and not scenario_ids:
        return {
            "suite_id": suite_id,
            "scenario_count": None,
            "covered_scenario_count": len(covered_ids),
            "coverage_percent": None,
            "covered_scenario_ids": covered_ids,
            "missing_scenario_ids": [],
            "covered_scenarios": [{"id": scenario_id, "title": scenario_id} for scenario_id in covered_ids],
            "missing_scenarios": [],
            "recommended_next_scenario": None,
            "coverage_status": "partial" if covered_ids else "empty",
        }

    covered_in_suite = [scenario_id for scenario_id in scenario_ids if scenario_id in covered_ids]
    missing_ids = [scenario_id for scenario_id in scenario_ids if scenario_id not in covered_ids]
    coverage_percent = round((len(covered_in_suite) / len(scenario_ids)) * 100, 2) if scenario_ids else None
    recommended_next_scenario = missing_ids[0] if missing_ids else None

    return {
        "suite_id": suite_id,
        "scenario_count": len(scenario_ids) if suite else None,
        "covered_scenario_count": len(covered_in_suite),
        "coverage_percent": coverage_percent,
        "covered_scenario_ids": covered_in_suite,
        "missing_scenario_ids": missing_ids,
        "covered_scenarios": [{"id": scenario_id, "title": scenario_titles[scenario_id]} for scenario_id in covered_in_suite],
        "missing_scenarios": [{"id": scenario_id, "title": scenario_titles[scenario_id]} for scenario_id in missing_ids],
        "recommended_next_scenario": {
            "id": recommended_next_scenario,
            "title": scenario_titles[recommended_next_scenario],
        } if recommended_next_scenario else None,
        "coverage_status": "complete" if scenario_ids and not missing_ids else "partial" if covered_in_suite else "empty",
    }

def _suite_failure_categories(record: dict[str, Any]) -> list[str]:
    suite_report = record.get('suite_report') if isinstance(record.get('suite_report'), dict) else {}
    categories: list[str] = []
    for summary in _scenario_summaries(suite_report):
        raw_categories = summary.get('failure_categories')
        if not isinstance(raw_categories, list):
            continue
        for category in raw_categories:
            if isinstance(category, str) and category.strip():
                categories.append(category.strip())
    return categories


def _top_failure_categories(category_counts: dict[str, int]) -> list[dict[str, Any]]:
    ranked = sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
    return [{'category': category, 'count': count} for category, count in ranked[:5]]


def _suite_history_vcon_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    available_records = 0
    dialog_turns = 0
    analysis_records = 0
    for record in records:
        suite_report = record.get('suite_report') if isinstance(record.get('suite_report'), dict) else {}
        for vcon_export in _suite_vcon_records(suite_report):
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


def _suite_score_from_record(record: dict[str, Any]) -> int | float | None:
    score = record.get('average_score')
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



def _suite_history_contract_artifact_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    suite_hashes: set[str] = set()
    scenario_hashes: set[str] = set()
    missing_records = 0

    for record in records:
        suite_hash = record.get('suite_contract_manifest_sha256')
        if isinstance(suite_hash, str) and suite_hash:
            suite_hashes.add(suite_hash)
        else:
            missing_records += 1

        suite_report = record.get('suite_report') if isinstance(record.get('suite_report'), dict) else {}
        scenario_runs = suite_report.get('scenario_runs') if isinstance(suite_report.get('scenario_runs'), list) else []
        for item in scenario_runs:
            report = item.get('benchmark_report') if isinstance(item, dict) and isinstance(item.get('benchmark_report'), dict) else item
            if not isinstance(report, dict):
                continue
            scenario_hash = report.get('scenario_contract_sha256')
            if isinstance(scenario_hash, str) and scenario_hash:
                scenario_hashes.add(scenario_hash)

    return {
        'available_records': len(records) - missing_records,
        'missing_records': missing_records,
        'total_runs': len(records),
        'suite_contract_manifest_sha256s': sorted(suite_hashes),
        'scenario_contract_sha256s': sorted(scenario_hashes),
    }


def _suite_run_progress(*, status: str, scenario_count: int, scenario_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    total = max(scenario_count, len(scenario_summaries), 0)
    completed = len(scenario_summaries)
    if status in TERMINAL_SUITE_STATUSES:
        completed = total
    if status == 'queued':
        completed = 0
    percent = 100 if total == 0 and status in TERMINAL_SUITE_STATUSES else round((completed / total) * 100) if total else 0
    if status == 'queued':
        phase = 'waiting'
    elif status == 'running':
        phase = 'executing'
    elif status in TERMINAL_SUITE_STATUSES:
        phase = 'finished'
    else:
        phase = 'unknown'
    return {
        'phase': phase,
        'active': status in {'queued', 'running'},
        'completed_scenarios': completed,
        'total_scenarios': total,
        'percent': percent,
    }


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
