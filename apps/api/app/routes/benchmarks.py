from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.benchmarks import BenchmarkRunRequest, BenchmarkSimulationRequest, BenchmarkSuiteRunRequest
from app.services.benchmark_service import get_suite, list_suites, run_scenario, run_suite, simulate_scenario, simulate_suite
from app.services.benchmark_run_store import get_benchmark_run, list_benchmark_runs, persist_benchmark_run
from app.services.benchmark_suite_run_store import (
    get_benchmark_suite_run,
    list_benchmark_suite_runs,
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


@router.get('/runs/{run_id}')
def get_benchmark_run_record(run_id: str, user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    record = get_benchmark_run(db=db, user_id=user_id, run_id=run_id)
    if record is None:
        raise HTTPException(status_code=404, detail='Benchmark run not found')
    return record


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
