from __future__ import annotations

from fastapi import APIRouter

from app.schemas.evals import EvalRunRequest, EvalRunResponse
from app.services.eval_service import run_eval

router = APIRouter(tags=['evals'])


@router.post('/api/evals/run', response_model=EvalRunResponse)
def run_voice_eval(payload: EvalRunRequest):
    return run_eval(payload)
