from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services import execution_run_store
from app.services.evaluation_contract import recorded_contract
from app.services import assert_judge_requests
from app.services.assert_benchmark_adapter import import_benchmark_review
from app.services.product_service import (
    find_visible_project,
    execution_project_accessible,
    record_judge_request,
    ensure_execution_product_project_id,
)
from app.services.upstream_assert_judge import (
    UpstreamAssertJudgeBudgetExceeded,
    UpstreamAssertJudgeBusy,
    UpstreamAssertJudgeFailed,
    UpstreamAssertJudgeUnavailable,
    run_upstream_assert_judge,
    assert_judge_readiness, assert_judge_input_fingerprint, _resolve_model,
)

router = APIRouter(prefix='/api/assert', tags=['assert-judge'])


def _saved_objects(value: object) -> list[dict]:
    """Disk-loaded evidence may contain invalid collections or list entries."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


class AssertExecutionJudgeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    user_id: str = Field(min_length=1)
    model_name: str | None = Field(default=None, min_length=1, max_length=160)
    judge_n: int = Field(default=1, ge=1, le=3)
    request_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r'^[A-Za-z0-9._-]+$')


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
            product_project_id = ensure_execution_product_project_id(
                db=db,
                user_id=payload.user_id,
                project_id=project_id,
                product_project_id=product_project_id,
            )
            if product_project_id:
                # Once a project has been resolved (or created), persist the
                # binding before judging. Later read/export/apply requests must
                # never have to guess which workspace shares this project key.
                run = execution_run_store.bind_execution_run_product_project(
                    execution_run_id,
                    user_id=payload.user_id,
                    project_id=project_id,
                    product_project_id=product_project_id,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=f'{exc} Rerun after selecting the exact personal or workspace project.',
            ) from exc

    deterministic_snapshot = execution_run_store.deterministic_evaluation_snapshot(conversation)
    try:
        scenario_contract = recorded_contract(conversation)
        model = _resolve_model(payload.model_name)
        fingerprint = assert_judge_input_fingerprint(run=run, conversation=conversation,
            scenario_contract=scenario_contract, model=model, judge_n=payload.judge_n)
        invocation_id, retained = assert_judge_requests.claim(db, scope={
            'user_id': payload.user_id, 'project_id': project_id, 'product_project_id': product_project_id,
            'execution_run_id': execution_run_id, 'conversation_id': conversation_id,
        }, fingerprint=fingerprint, request_id=payload.request_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        response = retained if retained is not None else run_upstream_assert_judge(
            run=run, conversation=conversation, scenario_contract=scenario_contract,
            model_name=model, judge_n=payload.judge_n,
        )
        if response.get('status') != 'ready':
            raise UpstreamAssertJudgeFailed('ASSERT did not produce a completed review.')
        response = {**response, 'invocation_id': invocation_id}
    except Exception as exc:
        assert_judge_requests.failed(db, invocation_id)
        code = 429 if isinstance(exc, (UpstreamAssertJudgeBusy, UpstreamAssertJudgeBudgetExceeded)) else (
            503 if isinstance(exc, UpstreamAssertJudgeUnavailable) else 422 if isinstance(exc, ValueError) else 502)
        raise HTTPException(status_code=code, detail=str(exc) if isinstance(exc, (ValueError, UpstreamAssertJudgeUnavailable,
            UpstreamAssertJudgeBusy, UpstreamAssertJudgeBudgetExceeded, UpstreamAssertJudgeFailed))
            else 'The semantic evaluator failed; the recorded agent result was not changed.') from exc

    if retained is None:
        try:
            assert_judge_requests.retain(db, invocation_id, response)
        except Exception as exc:
            db.rollback()
            # The provider may already have charged. Never mark this ambiguous
            # success retryable and accidentally repeat the paid call.
            raise HTTPException(status_code=503, detail=(
                'The judge completed but its output could not be persisted. '
                'This request remains reserved; inspect storage before explicitly requesting a new sample.'
            )) from exc

    # Retain first, then idempotently finalize review and audit. Failed finalization
    # can be retried without invoking the model or charging a second time.
    try:
        review = execution_run_store.record_judge_review(
            execution_run_id, conversation_id, user_id=payload.user_id, response=response,
            expected_deterministic_snapshot=deterministic_snapshot,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    judge_result = response.get('judge_result')
    agrees = judge_result.get('agrees') if isinstance(judge_result, dict) else None
    record_judge_request(
        db=db,
        request_id=invocation_id,
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

    result = {**response, 'review_id': review['review_id']}
    assert_judge_requests.retain(db, invocation_id, result, completed=True)
    return {**result, 'reused': retained is not None}


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
    conversation = next((item for item in _saved_objects(run.get('conversations'))
                         if item.get('conversation_id') == conversation_id), None)
    if conversation is None:
        raise HTTPException(status_code=404, detail='Conversation not found.')
    review = next((item for item in _saved_objects(conversation.get('judge_reviews'))
                   if item.get('review_id') == review_id), None)
    if review is None:
        raise HTTPException(status_code=409, detail='The selected saved ASSERT review is unavailable.')
    try:
        contract = recorded_contract(conversation)
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
    conversation = next((item for item in _saved_objects(run.get('conversations'))
                         if item.get('conversation_id') == conversation_id), None)
    if conversation is None:
        raise HTTPException(status_code=404, detail='Conversation not found.')
    review = next((item for item in _saved_objects(conversation.get('judge_reviews'))
                   if item.get('review_id') == review_id), None)
    if review is None:
        raise HTTPException(status_code=404, detail='Saved review not found.')
    contract = None  # freshness loads the immutable recorded snapshot, never the catalog
    return JSONResponse({'execution_run_id': execution_run_id, 'conversation_id': conversation_id, 'review_id': review_id,
            **saved_assert_review_freshness(run, conversation, review, contract)}, headers={'Cache-Control': 'private, no-store'})


@router.get('/readiness')
def judge_readiness():
    return JSONResponse(assert_judge_readiness(), headers={'Cache-Control': 'private, no-store'})


@router.post('/benchmarks/{benchmark_run_id}/judge')
def judge_benchmark(benchmark_run_id: str, payload: AssertExecutionJudgeRequest,
                    db: Session = Depends(get_db)):
    """Uploaded vCon/transcript and benchmark reviews share the execution judge."""
    try:
        run_id, conversation_id = import_benchmark_review(db, user_id=payload.user_id,
                                                          benchmark_run_id=benchmark_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response = judge_execution_conversation(run_id, conversation_id, payload, db)
    return {**response, 'execution_run_id': run_id, 'conversation_id': conversation_id,
            'source_benchmark_run_id': benchmark_run_id}
