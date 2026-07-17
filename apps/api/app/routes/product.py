from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.product import (
    CheckoutRequest,
    JudgeRequest,
    ProductProjectRequest,
    ProductProjectSettingsRequest,
    ProductWorkspaceInvitationAcceptRequest,
    ProductWorkspaceInvitationRequest,
    ProductWorkspaceMemberRequest,
    ProductWorkspaceRequest,
    SavedRunRequest,
)
from app.services.product_service import (
    accept_workspace_invitation,
    checkout_gate,
    add_workspace_member,
    disconnect_openai_provider,
    export_project_runs,
    export_saved_run,
    get_saved_run,
    list_audit_events,
    invite_workspace_member,
    judge_gate,
    list_llm_providers,
    list_openai_models,
    list_projects,
    list_saved_runs,
    list_workspaces,
    openai_provider_status,
    product_config,
    project_regression_summary,
    record_judge_request,
    save_run,
    start_openai_oauth,
    upsert_project,
    upsert_workspace,
    update_project_settings,
)

router = APIRouter(prefix='/api/product', tags=['product'])


@router.get('/config')
def get_product_config():
    return product_config()


@router.post('/checkout')
def create_checkout_gate(payload: CheckoutRequest):
    return checkout_gate(
        plan=payload.plan,
        user_id=payload.user_id,
        project_id=payload.project_id,
        success_url=payload.success_url,
        cancel_url=payload.cancel_url,
    )


@router.post('/workspaces')
def create_or_update_workspace(payload: ProductWorkspaceRequest, db: Session = Depends(get_db)):
    return upsert_workspace(
        db=db,
        owner_user_id=payload.owner_user_id,
        workspace_id=payload.workspace_id,
        name=payload.name,
        plan=payload.plan,
        settings=payload.settings,
        onboarding=payload.onboarding,
    )


@router.get('/workspaces')
def get_workspaces(user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    return list_workspaces(db=db, user_id=user_id)


@router.post('/workspaces/{workspace_id}/members')
def create_or_update_workspace_member(
    workspace_id: str,
    payload: ProductWorkspaceMemberRequest,
    db: Session = Depends(get_db),
):
    workspace = add_workspace_member(
        db=db,
        workspace_id=workspace_id,
        requester_user_id=payload.requester_user_id,
        user_id=payload.user_id,
        role=payload.role,
    )
    if workspace is None:
        raise HTTPException(status_code=404, detail='Workspace not found or requester lacks admin access')
    return workspace


@router.post('/workspaces/{workspace_id}/invitations')
def create_workspace_invitation(
    workspace_id: str,
    payload: ProductWorkspaceInvitationRequest,
    db: Session = Depends(get_db),
):
    invitation = invite_workspace_member(
        db=db,
        workspace_id=workspace_id,
        requester_user_id=payload.requester_user_id,
        email=payload.email,
        role=payload.role,
    )
    if invitation is None:
        raise HTTPException(status_code=404, detail='Workspace not found or requester lacks admin access')
    return invitation


@router.post('/workspaces/{workspace_id}/invitations/{invitation_id}/accept')
def accept_invitation(
    workspace_id: str,
    invitation_id: str,
    payload: ProductWorkspaceInvitationAcceptRequest,
    db: Session = Depends(get_db),
):
    workspace = accept_workspace_invitation(
        db=db,
        workspace_id=workspace_id,
        invitation_id=invitation_id,
        user_id=payload.user_id,
        email=payload.email,
    )
    if workspace is None:
        raise HTTPException(status_code=404, detail='Pending invitation not found for this email')
    return workspace


@router.post('/projects')
def create_or_update_project(payload: ProductProjectRequest, db: Session = Depends(get_db)):
    project = upsert_project(
        db=db,
        user_id=payload.user_id,
        workspace_id=payload.workspace_id,
        project_id=payload.project_id,
        name=payload.name,
        plan=payload.plan,
        settings=payload.settings,
        onboarding=payload.onboarding,
    )
    if project is None:
        raise HTTPException(status_code=404, detail='Workspace not found or user is not a member')
    return project


@router.get('/projects')
def get_projects(user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    return list_projects(db=db, user_id=user_id)


@router.get('/audit-events')
def get_audit_events(
    user_id: str = Query(min_length=1),
    project_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_audit_events(
        db=db,
        user_id=user_id,
        project_id=project_id,
        event_type=event_type,
        limit=limit,
    )


@router.patch('/projects/{project_id}/settings')
def patch_project_settings(project_id: str, payload: ProductProjectSettingsRequest, db: Session = Depends(get_db)):
    project = update_project_settings(db=db, project_id=project_id, payload=payload)
    if project is None:
        raise HTTPException(status_code=404, detail='Project not found')
    return project


@router.get('/projects/{project_id}/regression-summary')
def get_project_regression_summary(
    project_id: str,
    user_id: str = Query(min_length=1),
    suite_id: str | None = None,
    scenario_id: str | None = None,
    db: Session = Depends(get_db),
):
    summary = project_regression_summary(
        db=db,
        user_id=user_id,
        project_id=project_id,
        suite_id=suite_id,
        scenario_id=scenario_id,
    )
    if summary is None:
        raise HTTPException(status_code=404, detail='Project not found')
    return summary


@router.get('/projects/{project_id}/export')
def export_project_history(
    project_id: str,
    user_id: str = Query(min_length=1),
    suite_id: str | None = None,
    scenario_id: str | None = None,
    db: Session = Depends(get_db),
):
    exported = export_project_runs(
        db=db,
        user_id=user_id,
        project_id=project_id,
        suite_id=suite_id,
        scenario_id=scenario_id,
    )
    if exported is None:
        raise HTTPException(status_code=404, detail='Project not found')
    return exported


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
def get_saved_runs(
    user_id: str = Query(min_length=1),
    project_id: str | None = None,
    suite_id: str | None = None,
    scenario_id: str | None = None,
    db: Session = Depends(get_db),
):
    return list_saved_runs(
        db=db,
        user_id=user_id,
        project_id=project_id,
        suite_id=suite_id,
        scenario_id=scenario_id,
    )


@router.get('/runs/{run_id}')
def get_saved_run_report(run_id: str, user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    saved_run = get_saved_run(db=db, user_id=user_id, run_id=run_id)
    if saved_run is None:
        raise HTTPException(status_code=404, detail='Saved run not found')
    return saved_run


@router.get('/runs/{run_id}/export')
def export_run_report(run_id: str, user_id: str = Query(min_length=1), db: Session = Depends(get_db)):
    exported = export_saved_run(db=db, user_id=user_id, run_id=run_id)
    if exported is None:
        raise HTTPException(status_code=404, detail='Saved run not found')
    return exported


@router.post('/judge')
def request_llm_judge(payload: JudgeRequest, db: Session = Depends(get_db)):
    response = judge_gate(plan=payload.plan, report=payload.report, transcript=payload.transcript)
    if payload.user_id:
        agrees = response.judge_result.agrees if response.judge_result else None
        record_judge_request(
            db=db,
            user_id=payload.user_id,
            project_id=payload.project_id,
            plan=payload.plan,
            status=response.status,
            credits=response.credits,
            provider=response.provider,
            model=response.model,
            judge_output=response.judge_output,
            agrees=agrees,
        )
    return response


@router.get('/providers')
def get_llm_providers():
    return {'providers': list_llm_providers()}


@router.post('/providers/openai/oauth/start')
def start_openai_provider_oauth():
    try:
        return start_openai_oauth()
    except OSError as exc:
        raise HTTPException(status_code=409, detail=f'Could not start OpenAI OAuth callback server: {exc}') from exc


@router.get('/providers/openai/status')
def get_openai_provider_status():
    return openai_provider_status()


@router.get('/providers/openai/models')
def get_openai_provider_models():
    """Return available models for the connected OpenAI account.

    Always returns HTTP 200 when connected (or falls back to a curated list).
    Never surfaces raw upstream 403/scope JSON to the UI.
    """
    from app.services.llm_providers.openai_codex import (
        DEFAULT_EXECUTION_MODEL,
        FALLBACK_CHAT_MODELS,
        SCOPE_MISSING_MODELS_HINT,
    )

    try:
        return list_openai_models()
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001 - curated fallback beats a scary 502 banner
        return {
            'provider': 'openai_codex',
            'status': 'connected',
            'default_model': DEFAULT_EXECUTION_MODEL,
            'source': 'fallback',
            'message': SCOPE_MISSING_MODELS_HINT,
            'warning': SCOPE_MISSING_MODELS_HINT,
            'models': [{'id': model_id} for model_id in FALLBACK_CHAT_MODELS],
        }


@router.post('/providers/openai/disconnect')
def disconnect_openai_provider_route():
    return disconnect_openai_provider()
