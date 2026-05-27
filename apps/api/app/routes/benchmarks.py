from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.benchmarks import BenchmarkRunRequest, BenchmarkSimulationRequest
from app.services.benchmark_service import get_suite, list_suites, run_scenario, simulate_scenario

router = APIRouter(prefix='/api/benchmarks', tags=['benchmarks'])


@router.get('')
@router.get('/suites')
def list_benchmark_suites():
    return [get_suite(suite['id']) for suite in list_suites()]


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
def run_benchmark(payload: BenchmarkRunRequest):
    try:
        return run_scenario(payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/simulate')
def simulate_benchmark(payload: BenchmarkSimulationRequest):
    try:
        return simulate_scenario(payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/{suite_id}/scenarios/{scenario_id}/run')
def run_benchmark_scenario(suite_id: str, scenario_id: str, payload: BenchmarkRunRequest):
    merged_payload = payload.model_dump()
    merged_payload['suite_id'] = suite_id
    merged_payload['scenario_id'] = scenario_id
    try:
        return run_scenario(merged_payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post('/{suite_id}/scenarios/{scenario_id}/simulate')
def simulate_benchmark_scenario(suite_id: str, scenario_id: str, payload: BenchmarkSimulationRequest):
    merged_payload = payload.model_dump()
    merged_payload['suite_id'] = suite_id
    merged_payload['scenario_id'] = scenario_id
    try:
        return simulate_scenario(merged_payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
