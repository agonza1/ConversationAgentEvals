from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.routes.assert_judge import _product_plan
from app.services import execution_run_store
from app.services.benchmark_service import get_scenario_contract
from app.services.product_service import record_judge_request, resolve_execution_product_project_id
from app.services.openai_decisions_judge import (
    DecisionsJudgeBudgetExceeded, DecisionsJudgeBusy, DecisionsJudgeFailed,
    DecisionsJudgeUnavailable, run_openai_decisions_judge,
)

router = APIRouter(prefix='/api/decisions', tags=['decisions-judge'])


class DecisionsExecutionJudgeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str = Field(min_length=1)


@router.post('/runs/{execution_run_id}/conversations/{conversation_id}/judge')
def judge_execution_conversation(execution_run_id: str, conversation_id: str,
                                 payload: DecisionsExecutionJudgeRequest,
                                 db: Session = Depends(get_db)):
    """Opt-in bounded semantic review of persisted evidence, with no automatic application."""
    run = execution_run_store.get_execution_run(execution_run_id)
    if run is None or run.get('user_id') != payload.user_id:
        raise HTTPException(status_code=404, detail='Execution run not found.')
    conversation = execution_run_store.get_conversation(execution_run_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail='Conversation not found.')
    if run.get('status') in {'queued', 'running'} or conversation.get('status') in {'queued', 'running'}:
        raise HTTPException(status_code=409, detail='Run and conversation must be terminal before Decisions judging.')
    snapshot = execution_run_store.deterministic_evaluation_snapshot(conversation)
    if snapshot.get('verdict') not in {'pass', 'fail', 'failed', 'needs_review'}:
        raise HTTPException(status_code=409, detail='A deterministic verdict is required before Decisions judging.')
    project_id = str(run.get('project_id') or '').strip() or None
    product_project_id = str(run.get('product_project_id') or '').strip() or None
    if project_id:
        try:
            product_project_id = resolve_execution_product_project_id(
                db=db, user_id=payload.user_id, project_id=project_id,
                product_project_id=product_project_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Reloaded on server; callers cannot inject evidence, questions, model, or thresholds.
    contract = get_scenario_contract(str(conversation.get('suite_id') or run.get('suite_id') or ''),
                                     str(conversation.get('scenario_id') or ''))
    try:
        response = run_openai_decisions_judge(run=run, conversation=conversation, scenario_contract=contract)
    except (DecisionsJudgeBudgetExceeded, DecisionsJudgeBusy) as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except DecisionsJudgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DecisionsJudgeFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_judge_request(
        db=db, user_id=payload.user_id, project_id=project_id,
        product_project_id=product_project_id,
        plan=_product_plan(db, user_id=payload.user_id, project_id=project_id, product_project_id=product_project_id),
        status=response['status'], credits=response['credits'], provider=response['provider'],
        model=response['model'], judge_output=response['judge_output'], agrees=response['judge_result']['agrees'],
    )
    try:
        review = execution_run_store.record_judge_review(
            execution_run_id, conversation_id, user_id=payload.user_id,
            response=response, expected_deterministic_snapshot=snapshot,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**response, 'review_id': review['review_id']}
