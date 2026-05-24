from __future__ import annotations

from fastapi import APIRouter, Query

from app.schemas.product import JudgeRequest, SavedRunRequest
from app.services.product_service import judge_gate, list_saved_runs, product_config, save_run

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


@router.post('/judge')
def request_llm_judge(payload: JudgeRequest):
    return judge_gate(plan=payload.plan, report=payload.report, transcript=payload.transcript)
