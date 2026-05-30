from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import SessionLocal, get_db
from app.schemas.benchmarks import BenchmarkRunRequest, BenchmarkSimulationRequest, BenchmarkSuiteRunRequest
from app.services.benchmark_service import get_suite, list_suites, run_scenario, run_suite, simulate_scenario, simulate_suite
from app.services.benchmark_run_store import (
    export_benchmark_run_history,
    export_benchmark_run_vcon,
    get_benchmark_run,
    list_benchmark_runs,
    persist_benchmark_run,
)
from app.services.benchmark_suite_run_store import (
    create_benchmark_suite_run_record,
    export_benchmark_suite_run_vcon_bundle,
    get_benchmark_suite_run,
    list_benchmark_suite_runs,
    mark_benchmark_suite_run_failed,
    mark_benchmark_suite_run_running,
    persist_benchmark_suite_run,
)

router = APIRouter(prefix='/api/benchmarks', tags=['benchmarks'])


@router.get('')
@router.get('/suites')
def list_benchmark_suites():
    return [get_suite(suite['id']) for suite in list_suites()]


@router.get('/runs')
def get_benchmark_runs(
    user_id: str = Query(min_length=1),
    project_id: str | None = None,
    suite_id: str | None = None,
    scenario_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    return list_benchmark_runs(
        db=db,
        user_id=user_id,
        project_id=project_id,
        suite_id=suite_id,
        scenario_id=scenario_id,
        status=status,
    )


@router.get('/runs/export')
def export_benchmark_runs(
    user_id: str = Query(min_length=1),
    project_id: str | None = None,
    suite_id: str | None = None,
    scenario_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    return export_benchmark_run_history(
        db=db,
        user_id=user_id,
        project_id=project_id,
        suite_id=suite_id,
        scenario_id=scenario_id,
        status=status,
    )


@router.get('/suite-runs')
def get_benchmark_suite_runs(
    user_id: str = Query(min_length=1),
    project_id: str | None = None,
    suite_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    return list_benchmark_suite_runs(
        db=db,
        user_id=user_id,
        project_id=project_id,
        suite_id=suite_id,
        status=status,
    )


@router.get('/suite-runs/{suite_run_id}')
def get_benchmark_suite_run_record(suite_run_id: str, user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    record = get_benchmark_suite_run(db=db, user_id=user_id, suite_run_id=suite_run_id)
    if record is None:
        raise HTTPException(status_code=404, detail='Benchmark suite run not found')
    return record


@router.get('/suite-runs/{suite_run_id}/vcon-bundle')
def export_benchmark_suite_run_vcon_record_bundle(
    suite_run_id: str,
    user_id: str = Query(min_length=1),
    db: Session = Depends(get_db),
):
    exported = export_benchmark_suite_run_vcon_bundle(db=db, user_id=user_id, suite_run_id=suite_run_id)
    if exported is None:
        raise HTTPException(status_code=404, detail='Benchmark suite run not found')
    return exported


@router.get('/runs/{run_id}')
def get_benchmark_run_record(run_id: str, user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    record = get_benchmark_run(db=db, user_id=user_id, run_id=run_id)
    if record is None:
        raise HTTPException(status_code=404, detail='Benchmark run not found')
    return record


@router.get('/runs/{run_id}/vcon')
def export_benchmark_run_vcon_record(run_id: str, user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    exported = export_benchmark_run_vcon(db=db, user_id=user_id, run_id=run_id)
    if exported is None:
        raise HTTPException(status_code=404, detail='Benchmark run not found')
    return exported


@router.get('/{suite_id}')
@router.get('/suites/{suite_id}')
def get_benchmark_suite(suite_id: str):
    suite = get_suite(suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail='Benchmark suite not found.')
    return suite


@router.get('/{suite_id}/scenarios')
@router.get('/suites/{suite_id}/scenarios')
def list_benchmark_scenarios(suite_id: str):
    suite = get_suite(suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail='Benchmark suite not found.')
    return {'suite_id': suite_id, 'scenarios': suite['scenarios']}


@router.post('/{suite_id}/run-async')
@router.post('/suites/{suite_id}/run-async')
def enqueue_benchmark_suite_run(
    suite_id: str,
    payload: BenchmarkSuiteRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    merged_payload = payload.model_dump()
    merged_payload['suite_id'] = suite_id
    suite_run_id = _queued_suite_run_id(suite_id=suite_id, payload=merged_payload)
    try:
        queued_record = create_benchmark_suite_run_record(
            db=db,
            suite_run_id=suite_run_id,
            suite_id=suite_id,
            metadata=_metadata_from_payload(merged_payload),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    background_tasks.add_task(_execute_suite_run_background, suite_run_id, suite_id, merged_payload, False)
    return queued_record


@router.post('/{suite_id}/simulate-async')
@router.post('/suites/{suite_id}/simulate-async')
def enqueue_benchmark_suite_simulation(
    suite_id: str,
    payload: BenchmarkSimulationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    merged_payload = payload.model_dump()
    merged_payload['suite_id'] = suite_id
    suite_run_id = _queued_suite_run_id(suite_id=suite_id, payload=merged_payload)
    try:
        queued_record = create_benchmark_suite_run_record(
            db=db,
            suite_run_id=suite_run_id,
            suite_id=suite_id,
            metadata=_metadata_from_payload(merged_payload),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    background_tasks.add_task(_execute_suite_run_background, suite_run_id, suite_id, merged_payload, True)
    return queued_record


@router.post('/run')
def run_benchmark(payload: BenchmarkRunRequest, db: Session = Depends(get_db)):
    try:
        report = run_scenario(payload)
        persist_benchmark_run(db=db, report=report, transcript=payload.transcript)
        return report
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/{suite_id}/run')
@router.post('/suites/{suite_id}/run')
def run_benchmark_suite(suite_id: str, payload: BenchmarkSuiteRunRequest, db: Session = Depends(get_db)):
    merged_payload = payload.model_dump()
    merged_payload['suite_id'] = suite_id
    try:
        suite_report = run_suite(merged_payload)
        persist_benchmark_suite_run(db=db, suite_report=suite_report)
        for report in suite_report.get('scenario_reports', []):
            if isinstance(report, dict):
                persist_benchmark_run(db=db, report=report, transcript=report.get('transcript_preview'))
        return suite_report
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/simulate')
def simulate_benchmark(payload: BenchmarkSimulationRequest, db: Session = Depends(get_db)):
    try:
        simulation = simulate_scenario(payload)
        persist_benchmark_run(db=db, report=simulation['benchmark_report'], transcript=simulation['transcript'])
        return simulation
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/{suite_id}/simulate')
@router.post('/suites/{suite_id}/simulate')
def simulate_benchmark_suite(suite_id: str, payload: BenchmarkSimulationRequest, db: Session = Depends(get_db)):
    merged_payload = payload.model_dump()
    merged_payload['suite_id'] = suite_id
    try:
        simulation = simulate_suite(merged_payload)
        persist_benchmark_suite_run(db=db, suite_report=simulation)
        for scenario_run in simulation.get('scenario_runs', []):
            if isinstance(scenario_run, dict) and isinstance(scenario_run.get('benchmark_report'), dict):
                persist_benchmark_run(
                    db=db,
                    report=scenario_run['benchmark_report'],
                    transcript=scenario_run.get('transcript'),
                )
        return simulation
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/{suite_id}/scenarios/{scenario_id}/run')
def run_benchmark_scenario(suite_id: str, scenario_id: str, payload: BenchmarkRunRequest, db: Session = Depends(get_db)):
    merged_payload = payload.model_dump()
    merged_payload['suite_id'] = suite_id
    merged_payload['scenario_id'] = scenario_id
    try:
        report = run_scenario(merged_payload)
        persist_benchmark_run(db=db, report=report, transcript=payload.transcript)
        return report
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/{suite_id}/scenarios/{scenario_id}/simulate')
def simulate_benchmark_scenario(suite_id: str, scenario_id: str, payload: BenchmarkSimulationRequest, db: Session = Depends(get_db)):
    merged_payload = payload.model_dump()
    merged_payload['suite_id'] = suite_id
    merged_payload['scenario_id'] = scenario_id
    try:
        simulation = simulate_scenario(merged_payload)
        persist_benchmark_run(db=db, report=simulation['benchmark_report'], transcript=simulation['transcript'])
        return simulation
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _execute_suite_run_background(suite_run_id: str, suite_id: str, payload: dict[str, Any], simulate: bool) -> None:
    with SessionLocal() as db:
        mark_benchmark_suite_run_running(db=db, suite_run_id=suite_run_id)
        try:
            suite_report = simulate_suite(payload) if simulate else run_suite(payload)
            suite_report = _with_suite_run_id(suite_report, suite_run_id)
            persist_benchmark_suite_run(db=db, suite_report=suite_report)
            if simulate:
                for scenario_run in suite_report.get('scenario_runs', []):
                    if isinstance(scenario_run, dict) and isinstance(scenario_run.get('benchmark_report'), dict):
                        persist_benchmark_run(
                            db=db,
                            report=scenario_run['benchmark_report'],
                            transcript=scenario_run.get('transcript'),
                        )
            else:
                for report in suite_report.get('scenario_reports', []):
                    if isinstance(report, dict):
                        persist_benchmark_run(db=db, report=report, transcript=report.get('transcript_preview'))
        except Exception as exc:  # Background tasks must retain failures in run history.
            mark_benchmark_suite_run_failed(db=db, suite_run_id=suite_run_id, error=str(exc))


def _queued_suite_run_id(*, suite_id: str, payload: dict[str, Any]) -> str:
    import hashlib
    import json

    fingerprint = json.dumps({'suite_id': suite_id, 'payload': payload}, sort_keys=True, default=str)
    return hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]


def _metadata_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    merged = dict(metadata)
    for key in ('user_id', 'project_id'):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    for source, target in (('agent_version', 'agent_version'), ('agentVersion', 'agent_version'), ('prompt_version', 'prompt_version'), ('promptVersion', 'prompt_version'), ('model_name', 'model_name'), ('modelName', 'model_name'), ('notes', 'notes')):
        value = payload.get(source)
        if isinstance(value, str) and value.strip():
            merged[target] = value.strip()
    return merged


def _with_suite_run_id(suite_report: dict[str, Any], suite_run_id: str) -> dict[str, Any]:
    updated = deepcopy(suite_report)
    updated['suite_run_id'] = suite_run_id
    vcon_export = updated.get('vcon_export')
    if isinstance(vcon_export, dict):
        for analysis in vcon_export.get('analysis', []):
            body = analysis.get('body') if isinstance(analysis, dict) else None
            if isinstance(body, dict):
                body['suite_run_id'] = suite_run_id
    return updated
