from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services import execution_run_store
from app.services.benchmark_service import get_scenario_contract
from app.services.product_service import (
    find_visible_project,
    execution_project_accessible,
    record_judge_request,
    resolve_execution_product_project_id,
)
from app.services.upstream_assert_judge import (
    UpstreamAssertJudgeBudgetExceeded,
    UpstreamAssertJudgeBusy,
    UpstreamAssertJudgeFailed,
    UpstreamAssertJudgeUnavailable,
    run_upstream_assert_judge,
)

router = APIRouter(prefix='/api/assert', tags=['assert-judge'])


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


@router.post('/runs/{execution_run_id}/conversations/{conversation_id}/judge')
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

    project_id = str(run.get('project_id') or '').strip() or None
    product_project_id = str(run.get('product_project_id') or '').strip() or None
    if project_id:
        try:
            product_project_id = resolve_execution_product_project_id(
                db=db,
                user_id=payload.user_id,
                project_id=project_id,
                product_project_id=product_project_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=f'{exc} Rerun after selecting the exact personal or workspace project.',
            ) from exc

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


@router.get('/runs/{execution_run_id}/conversations/{conversation_id}/reviews/{review_id}/report.html')
def export_assert_html_report(execution_run_id: str, conversation_id: str, review_id: str,
                              user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    """Download the specified persisted review without invoking paid judging."""
    from app.services.assert_html_report import validate_saved_review, render_assert_html_report, report_filename

    run = execution_run_store.get_execution_run(execution_run_id)
    if run is None or run.get('user_id') != user_id:
        raise HTTPException(status_code=404, detail='Execution run not found.')
    project_id = str(run.get('project_id') or '').strip()
    if not execution_project_accessible(db=db, user_id=user_id, project_id=project_id,
                                        product_project_id=run.get('product_project_id')):
        raise HTTPException(status_code=404, detail='Execution run not found.')
    # Use the same saved run snapshot for evidence and selected review, avoiding
    # a second store read that could mix different revisions during an update.
    conversation = next((item for item in run.get('conversations') or []
                         if item.get('conversation_id') == conversation_id), None)
    if conversation is None:
        raise HTTPException(status_code=404, detail='Conversation not found.')
    review = next((item for item in conversation.get('judge_reviews') or []
                   if item.get('review_id') == review_id), None)
    if review is None:
        raise HTTPException(status_code=409, detail='The selected saved ASSERT review is unavailable.')
    try:
        contract = get_scenario_contract(str(conversation.get('suite_id') or run.get('suite_id') or ''),
                                         str(conversation.get('scenario_id') or ''))
        provenance = validate_saved_review(run, conversation, review, contract)
        content = render_assert_html_report(run, conversation, review, provenance)
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc) if isinstance(exc, ValueError)
                            else 'Saved ASSERT evidence is malformed or unavailable.') from exc
    filename = report_filename(execution_run_id, conversation_id, review_id)
    return HTMLResponse(content, headers={
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
    })


@router.get('/runs/{execution_run_id}/conversations/{conversation_id}/reviews/{review_id}/status')
def saved_review_status(execution_run_id: str, conversation_id: str, review_id: str,
                        user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    from app.services.assert_review_status import saved_assert_review_freshness
    run = execution_run_store.get_execution_run(execution_run_id)
    if run is None or run.get('user_id') != user_id:
        raise HTTPException(status_code=404, detail='Execution run not found.')
    project_id = str(run.get('project_id') or '').strip()
    if not execution_project_accessible(db=db, user_id=user_id, project_id=project_id,
                                        product_project_id=run.get('product_project_id')):
        raise HTTPException(status_code=404, detail='Execution run not found.')
    conversation = next((item for item in run.get('conversations') or []
                         if isinstance(item, dict) and item.get('conversation_id') == conversation_id), None)
    if conversation is None:
        raise HTTPException(status_code=404, detail='Conversation not found.')
    review = next((item for item in conversation.get('judge_reviews') or []
                   if isinstance(item, dict) and item.get('review_id') == review_id), None)
    if review is None:
        raise HTTPException(status_code=404, detail='Saved review not found.')
    contract = get_scenario_contract(str(conversation.get('suite_id') or run.get('suite_id') or ''),
                                    str(conversation.get('scenario_id') or ''))
    return JSONResponse({'execution_run_id': execution_run_id, 'conversation_id': conversation_id, 'review_id': review_id,
            **saved_assert_review_freshness(run, conversation, review, contract)}, headers={'Cache-Control': 'private, no-store'})
