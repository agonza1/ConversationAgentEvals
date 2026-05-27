from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.product import JudgeRequest, ProductProjectRequest, SavedRunRequest
from app.services.product_service import (
    export_saved_run,
    judge_gate,
    list_projects,
    list_saved_runs,
    product_config,
    save_run,
    upsert_project,
)

router = APIRouter(prefix='/api/product', tags=['product'])


@router.get('/config')
def get_product_config():
    return product_config()


@router.post('/projects')
def create_or_update_project(payload: ProductProjectRequest, db: Session = Depends(get_db)):
    return upsert_project(
        db=db,
        user_id=payload.user_id,
        project_id=payload.project_id,
        name=payload.name,
        plan=payload.plan,
    )


@router.get('/projects')
def get_projects(user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    return list_projects(db=db, user_id=user_id)


@router.post('/runs')
def create_saved_run(payload: SavedRunRequest, db: Session = Depends(get_db)):
    return save_run(
        db=db,
        user_id=payload.user_id,
        project_id=payload.project_id,
        plan=payload.plan,
        report=payload.report,
        transcript=payload.transcript,
    )


@router.get('/runs')
def get_saved_runs(user_id: str = Query(min_length=1), project_id: str | None = None, db: Session = Depends(get_db)):
    return list_saved_runs(db=db, user_id=user_id, project_id=project_id)


@router.get('/runs/{run_id}/export')
def export_run_report(run_id: str, user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    exported = export_saved_run(db=db, user_id=user_id, run_id=run_id)
    if exported is None:
        raise HTTPException(status_code=404, detail='Saved run not found')
    return exported


@router.post('/judge')
def request_llm_judge(payload: JudgeRequest):
    return judge_gate(plan=payload.plan, report=payload.report, transcript=payload.transcript)
