from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.assert_contracts import AssertRunCreateRequest
from app.services.assert_sidecar import create_local_assert_sidecar_run, load_local_assert_sidecar_run

router = APIRouter(prefix='/api/assert', tags=['assert'])


@router.post('/runs')
def create_assert_sidecar_run(payload: AssertRunCreateRequest):
    record = create_local_assert_sidecar_run(payload)
    return record.model_dump(mode='json')


@router.get('/runs/{platform_run_id}')
def get_assert_sidecar_run(platform_run_id: str):
    saved = load_local_assert_sidecar_run(platform_run_id)
    if saved is None:
        raise HTTPException(status_code=404, detail='ASSERT sidecar run not found')
    return saved['record']
