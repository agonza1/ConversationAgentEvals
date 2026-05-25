from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.product import JudgeRequest, SavedRunRequest
from app.services.product_service import export_saved_run, judge_gate, list_saved_runs, product_config, save_run

router = APIRouter(prefix='/api/product', tags=['product'])


@router.get('/config')
def get_product_config():
    return product_config()


@router.post('/runs')
def create_saved_run(payload: SavedRunRequest):
    return save_run(
        user_id=payload.user_id,
        project_id=payload.project_id,
        plan=payload.plan,
        report=payload.report,
        transcript=payload.transcript,
    )


@router.get('/runs')
def get_saved_runs(user_id: str = Query(min_length=1), project_id: str | None = None):
    return list_saved_runs(user_id=user_id, project_id=project_id)


@router.get('/runs/{run_id}/export')
def export_run_report(run_id: str, user_id: str = Query(min_length=1)):
    exported = export_saved_run(user_id=user_id, run_id=run_id)
    if exported is None:
        raise HTTPException(status_code=404, detail='Saved run not found')
    return exported


@router.post('/judge')
def request_llm_judge(payload: JudgeRequest):
    return judge_gate(plan=payload.plan, report=payload.report, transcript=payload.transcript)
