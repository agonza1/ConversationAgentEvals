from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import SessionLocal, get_db
from app.schemas.benchmarks import BenchmarkRunRequest, BenchmarkSimulationRequest, BenchmarkSuiteRunRequest
from app.services.benchmark_service import (
    get_scenario_contract,
    _run_metadata,
    _first_string,
    get_suite,
    get_suite_contract_manifest,
    list_suites,
    run_scenario,
    run_suite,
    simulate_scenario,
    simulate_suite,
    validate_suite_evidence,
)
from app.services.benchmark_run_store import (
    DEFAULT_PROJECT_ID,
    DEFAULT_USER_ID,
    export_benchmark_run_history,
    export_benchmark_run_vcon,
    get_benchmark_run,
    get_benchmark_run_audit_artifacts,
    list_benchmark_runs,
    persist_benchmark_run,
)
from app.services.benchmark_suite_run_store import (
    create_benchmark_suite_run_record,
    export_benchmark_suite_run_vcon_bundle,
    export_benchmark_suite_run_history,
    get_benchmark_suite_run,
    get_benchmark_suite_run_audit_artifacts,
    list_benchmark_suite_runs,
    mark_benchmark_suite_run_failed,
    mark_benchmark_suite_run_running,
    persist_benchmark_suite_run,
)

router = APIRouter(prefix='/api/benchmarks', tags=['benchmarks'])


def _bind_benchmark_project(db: Session, payload: dict[str, Any], *,
                            suite_run: bool = False, simulation: bool = False) -> dict[str, Any]:
    """Resolve the exact project before any evidence-derived run ID is computed.

    The DB-backed UUID is part of the run fingerprint and must never first
    appear as a post-evaluation persistence side effect. Keep the same identity
    for direct, simulated, suite, and queued benchmark executions.
    """
    from app.services.product_service import ensure_execution_product_project_id

    validation_payload = payload
    if not suite_run and not simulation:
        # vCon evidence can carry its own target IDs; use the same read-only
        # normalization as run_scenario rather than requiring explicit IDs.
        from app.services.assert_adapter import normalize_assert_payload
        from app.services.vcon_evidence import intake_vcon
        validation_payload, _ = normalize_assert_payload(payload)
        validation_payload, _ = intake_vcon(validation_payload)
    suite_id = _first_string(validation_payload, 'suite_id', 'suiteId')
    scenario_id = _first_string(validation_payload, 'scenario_id', 'scenarioId')
    if not suite_id:
        raise ValueError('suite_id is required')
    suite = get_suite(suite_id)
    if suite is None:
        raise ValueError(f'Unknown benchmark suite: {suite_id}')
    if suite_run:
        if not simulation:
            validate_suite_evidence(suite, payload)
    elif not scenario_id:
        raise ValueError('scenario_id is required')
    elif get_scenario_contract(suite_id, scenario_id) is None:
        raise ValueError(f'Unknown benchmark scenario: {suite_id}/{scenario_id}')

    metadata = _run_metadata(payload)
    user_id = metadata.get('user_id') or DEFAULT_USER_ID
    project_id = metadata.get('project_id') or DEFAULT_PROJECT_ID
    product_project_id = ensure_execution_product_project_id(
        db=db,
        user_id=user_id,
        project_id=project_id,
        product_project_id=metadata.get('product_project_id'),
    )
    return {**payload, 'user_id': user_id, 'project_id': project_id,
            'product_project_id': product_project_id}


class VconIntakeRequest(BaseModel):
    vcon: dict[str, Any]


class TelemetryVconRequest(BaseModel):
    format: str
    data: dict[str, Any]
    transcript: str = ''
    suite_id: str | None = None
    scenario_id: str | None = None
    final_state: dict[str, Any] | None = None
    synthetic: bool = False


@router.post('/evidence/telemetry-vcon')
def source_telemetry_vcon(payload: TelemetryVconRequest):
    from app.services.voice_telemetry_vcon import build_telemetry_vcon
    try:
        return build_telemetry_vcon(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/evidence/intake')
def inspect_vcon_evidence(payload: VconIntakeRequest):
    from app.services.vcon_evidence import intake_vcon
    try:
        evidence, summary = intake_vcon({'vcon': payload.vcon})
        from app.services.vcon_interop import vcon_dialog_turns
        evidence.setdefault('transcript', '\n'.join(vcon_dialog_turns(payload.vcon)))
        return {'evidence': evidence, 'summary': summary}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/evidence/sample-vcon')
def sample_vcon_evidence(suite_id: str, scenario_id: str):
    from app.services.vcon_evidence import build_benchmark_vcon
    suite = get_suite(suite_id)
    scenario = next((s for s in (suite or {}).get('scenarios', []) if s['id'] == scenario_id), None)
    if scenario is None:
        raise HTTPException(status_code=404, detail='Benchmark scenario not found')
    # Only authored fixtures may gain synthetic links. Never infer links for
    # arbitrary imported evidence or a custom transcript/action-trace pair.
    from app.services.benchmark_service import _simulated_action_trace, _simulated_transcript
    from app.services.benchmark_catalog_extensions import (
        _cancellation_rescue_sample_action_trace, _cancellation_rescue_sample_transcript,
    )
    transcript = scenario.get('sample_transcript') or ''
    actions = scenario.get('sample_action_trace') or []
    if (transcript == _simulated_transcript(scenario, 'starter sample agent', False)
            and actions == _simulated_action_trace(scenario, False)):
        actions = _simulated_action_trace(scenario, False, include_dialog_links=True)
    elif (transcript == _cancellation_rescue_sample_transcript()
          and actions == _cancellation_rescue_sample_action_trace()):
        actions = _cancellation_rescue_sample_action_trace(include_dialog_links=True)
    payload = {'suite_id': suite_id, 'scenario_id': scenario_id,
               'action_trace': actions,
               'final_state': scenario.get('sample_final_state') or {}}
    return build_benchmark_vcon(payload, transcript, synthetic=True)


@router.get('')
@router.get('/suites')
def list_benchmark_suites():
    # list_suites refreshes the published catalog once for this request.
    return [get_suite(suite['id'], refresh=False) for suite in list_suites()]


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


@router.get('/suite-runs/export')
def export_benchmark_suite_runs(
    user_id: str = Query(min_length=1),
    project_id: str | None = None,
    suite_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    return export_benchmark_suite_run_history(
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


@router.get('/suite-runs/{suite_run_id}/audit-artifacts')
def get_benchmark_suite_run_audit_artifact_view(
    suite_run_id: str,
    user_id: str = Query(min_length=1),
    db: Session = Depends(get_db),
):
    view = get_benchmark_suite_run_audit_artifacts(db=db, user_id=user_id, suite_run_id=suite_run_id)
    if view is None:
        raise HTTPException(status_code=404, detail='Benchmark suite run not found')
    return view


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


@router.get('/runs/{run_id}/audit-artifacts')
def get_benchmark_run_audit_artifact_view(run_id: str, user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    view = get_benchmark_run_audit_artifacts(db=db, user_id=user_id, run_id=run_id)
    if view is None:
        raise HTTPException(status_code=404, detail='Benchmark run not found')
    return view


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


@router.get('/{suite_id}/contract-manifest')
@router.get('/suites/{suite_id}/contract-manifest')
def get_benchmark_suite_contract_manifest(suite_id: str):
    manifest = get_suite_contract_manifest(suite_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail='Benchmark suite not found.')
    return manifest


@router.get('/{suite_id}/scenarios')
@router.get('/suites/{suite_id}/scenarios')
def list_benchmark_scenarios(suite_id: str):
    suite = get_suite(suite_id)
    if suite is None:
        raise HTTPException(status_code=404, detail='Benchmark suite not found.')
    return {'suite_id': suite_id, 'scenarios': suite['scenarios']}


@router.get('/{suite_id}/scenarios/{scenario_id}/contract')
@router.get('/suites/{suite_id}/scenarios/{scenario_id}/contract')
def get_benchmark_scenario_contract(suite_id: str, scenario_id: str):
    contract = get_scenario_contract(suite_id, scenario_id)
    if contract is None:
        raise HTTPException(status_code=404, detail='Benchmark scenario not found.')
    return contract


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
    try:
        merged_payload = _bind_benchmark_project(db, merged_payload, suite_run=True)
        suite_run_id = _queued_suite_run_id(suite_id=suite_id, payload=merged_payload)
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
    try:
        merged_payload = _bind_benchmark_project(db, merged_payload, suite_run=True, simulation=True)
        suite_run_id = _queued_suite_run_id(suite_id=suite_id, payload=merged_payload)
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
        report = run_scenario(_bind_benchmark_project(db, payload.model_dump()))
        persist_benchmark_run(db=db, report=report, transcript=payload.transcript)
        return report
    except ValueError as exc:
        raise HTTPException(status_code=404 if str(exc).startswith('Unknown benchmark') else 422, detail=str(exc)) from exc


@router.post('/{suite_id}/run')
@router.post('/suites/{suite_id}/run')
def run_benchmark_suite(suite_id: str, payload: BenchmarkSuiteRunRequest, db: Session = Depends(get_db)):
    merged_payload = payload.model_dump()
    merged_payload['suite_id'] = suite_id
    try:
        suite_report = run_suite(_bind_benchmark_project(db, merged_payload, suite_run=True))
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
        simulation = simulate_scenario(_bind_benchmark_project(db, payload.model_dump(), simulation=True))
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
        simulation = simulate_suite(_bind_benchmark_project(db, merged_payload, suite_run=True, simulation=True))
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
        report = run_scenario(_bind_benchmark_project(db, merged_payload))
        persist_benchmark_run(db=db, report=report, transcript=payload.transcript)
        return report
    except ValueError as exc:
        raise HTTPException(status_code=404 if str(exc).startswith('Unknown benchmark') else 422, detail=str(exc)) from exc


@router.post('/{suite_id}/scenarios/{scenario_id}/simulate')
def simulate_benchmark_scenario(suite_id: str, scenario_id: str, payload: BenchmarkSimulationRequest, db: Session = Depends(get_db)):
    merged_payload = payload.model_dump()
    merged_payload['suite_id'] = suite_id
    merged_payload['scenario_id'] = scenario_id
    try:
        simulation = simulate_scenario(_bind_benchmark_project(db, merged_payload, simulation=True))
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
    for key in ('user_id', 'project_id', 'product_project_id'):
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
