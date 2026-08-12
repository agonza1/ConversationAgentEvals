from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.assert_contracts import AssertRunCreateRequest
from app.services import execution_run_store
from app.services.assert_sidecar import create_local_assert_sidecar_run, load_local_assert_sidecar_run
from app.services.benchmark_service import get_scenario_contract
from app.services.product_service import find_visible_project, record_judge_request
from app.services.upstream_assert_judge import (
    UpstreamAssertJudgeBudgetExceeded,
    UpstreamAssertJudgeBusy,
    UpstreamAssertJudgeFailed,
    UpstreamAssertJudgeUnavailable,
    run_upstream_assert_judge,
)

# Local sidecar lifecycle routes remain development-only.
router = APIRouter(prefix='/api/assert', tags=['assert'])
# Product judgment is mounted independently so production deployments can use it
# while the local sidecar lifecycle remains disabled.
judge_router = APIRouter(prefix='/api/assert', tags=['assert-judge'])


class AssertExecutionJudgeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    user_id: str = Field(min_length=1)
    model_name: str | None = Field(default=None, min_length=1, max_length=160)
    judge_n: int = Field(default=1, ge=1, le=3)


def _product_plan(
    db: Session,
    *,
    user_id: str,
    project_id: str | None,
    product_project_id: str | None = None,
) -> str:
    """Return the persisted project plan without treating a feature requirement as entitlement."""
    if not project_id:
        return 'free'
    project = find_visible_project(
        db=db,
        user_id=user_id,
        project_id=project_id,
        product_project_id=product_project_id,
    )
    plan = str(project.plan or '').strip().lower() if project is not None else ''
    return plan if plan in {'free', 'starter', 'team', 'business'} else 'free'


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


@judge_router.post('/runs/{execution_run_id}/conversations/{conversation_id}/judge')
def judge_execution_conversation(
    execution_run_id: str,
    conversation_id: str,
    payload: AssertExecutionJudgeRequest,
    db: Session = Depends(get_db),
):
    """Run upstream ASSERT judging over completed CAE text or voice evidence."""
    run = execution_run_store.get_execution_run(execution_run_id)
    if run is None or run.get('user_id') != payload.user_id:
        raise HTTPException(status_code=404, detail='Execution run not found.')
    if run.get('status') in {'queued', 'running'}:
        raise HTTPException(status_code=409, detail='The execution run must be terminal before ASSERT judging.')
    conversation = execution_run_store.get_conversation(execution_run_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail='Conversation not found.')
    if conversation.get('status') in {'queued', 'running'}:
        raise HTTPException(status_code=409, detail='The conversation must be terminal before ASSERT judging.')
    deterministic_verdict = str(conversation.get('verdict') or '').strip().lower()
    if deterministic_verdict not in {'pass', 'needs_review', 'fail', 'failed'}:
        raise HTTPException(
            status_code=409,
            detail='The conversation must have a deterministic verdict before ASSERT judging.',
        )

    deterministic_snapshot = execution_run_store.deterministic_evaluation_snapshot(conversation)
    scenario_contract = get_scenario_contract(
        str(conversation.get('suite_id') or run.get('suite_id') or ''),
        str(conversation.get('scenario_id') or ''),
    )
    try:
        response = run_upstream_assert_judge(
            run=run,
            conversation=conversation,
            scenario_contract=scenario_contract,
            model_name=payload.model_name,
            judge_n=payload.judge_n,
        )
    except (UpstreamAssertJudgeBusy, UpstreamAssertJudgeBudgetExceeded) as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except UpstreamAssertJudgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UpstreamAssertJudgeFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    judge_result = response.get('judge_result')
    agrees = judge_result.get('agrees') if isinstance(judge_result, dict) else None
    project_id = str(run.get('project_id') or '').strip() or None
    product_project_id = str(run.get('product_project_id') or '').strip() or None
    record_judge_request(
        db=db,
        user_id=payload.user_id,
        project_id=project_id,
        plan=_product_plan(
            db,
            user_id=payload.user_id,
            project_id=project_id,
            product_project_id=product_project_id,
        ),
        status=str(response.get('status') or 'ready'),
        credits=int(response.get('credits') or 0),
        product_project_id=product_project_id,
        provider=str(response.get('provider') or '').strip() or None,
        model=str(response.get('model') or '').strip() or None,
        judge_output=str(response.get('judge_output') or '').strip() or None,
        agrees=agrees if isinstance(agrees, bool) else None,
    )

    try:
        review = execution_run_store.record_judge_review(
            execution_run_id,
            conversation_id,
            user_id=payload.user_id,
            response=response,
            expected_deterministic_snapshot=deterministic_snapshot,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**response, 'review_id': review['review_id']}
