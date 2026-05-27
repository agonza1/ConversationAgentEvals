from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.entities import (
    ProductProject,
    ProductSavedRun,
    ProductWorkspace,
    ProductWorkspaceInvitation,
    ProductWorkspaceMember,
)
from app.schemas.product import (
    FirebaseAuthConfig,
    JudgeResponse,
    PricingPlan,
    ProductProjectSettingsRequest,
    ProductProjectResponse,
    ProductWorkspaceInvitationResponse,
    ProductWorkspaceMemberResponse,
    ProductWorkspaceResponse,
    ProductConfig,
    SavedRunExportResponse,
    SavedRunResponse,
    UsageRule,
)


PRICING = [
    PricingPlan(
        id='free',
        name='Free',
        price_label='$0',
        seats='Single browser workspace',
        included_credits=25,
        cta='Run free eval',
        features=[
            'Browser transcript evals',
            'Deterministic checks',
            'Sample call-center benchmarks',
            'Local report preview',
        ],
    ),
    PricingPlan(
        id='starter',
        name='Starter',
        price_label='$19/month',
        seats='Unlimited seats',
        included_credits=500,
        cta='Start Starter',
        features=[
            'Saved projects and run history',
            'Custom benchmark suites',
            'Credits-based LLM judge runs',
            'Report export',
        ],
    ),
    PricingPlan(
        id='team',
        name='Team',
        price_label='$99/month',
        seats='Unlimited seats',
        included_credits=3500,
        cta='Start Team',
        features=[
            'Higher included run and judge credits',
            'CI/API access',
            'Version comparisons',
            'Shared audit history',
            'Voice/WebRTC test run access',
        ],
    ),
    PricingPlan(
        id='business',
        name='Business',
        price_label='Contact Us',
        seats='Unlimited seats',
        included_credits=None,
        cta='Contact us',
        features=[
            'Custom integration',
            'Readiness assessment',
            'Consulting and benchmark design',
            'High-volume evals',
            'Phone/SIP and compliance exports',
        ],
    ),
]

USAGE_RULES = [
    UsageRule(id='deterministic_eval', label='Deterministic browser eval', credits=1),
    UsageRule(id='llm_judge', label='Evidence-grounded LLM judge', credits=10, gated_plan='starter'),
    UsageRule(id='voice_webrtc_minute', label='Voice/WebRTC eval minute', credits=5, gated_plan='team'),
    UsageRule(id='api_ci_run', label='CI/API benchmark run', credits=3, gated_plan='team'),
]


def product_config() -> ProductConfig:
    return ProductConfig(
        pricing=PRICING,
        usage_rules=USAGE_RULES,
        auth=_firebase_auth_config(),
        voice_status='gated',
        llm_judge_status='gated',
    )


DEFAULT_WORKSPACE_SETTINGS = {
    'default_benchmark_suite': 'call-center-support',
    'report_visibility': 'workspace',
    'retention_days': 90,
}

DEFAULT_ONBOARDING = {
    'sample_project_created': True,
    'next_step': 'run_first_benchmark',
    'checklist': ['create_workspace', 'invite_teammate', 'run_benchmark', 'export_report'],
}


def upsert_workspace(
    db: Session,
    owner_user_id: str,
    workspace_id: str,
    name: str,
    plan: str,
    settings: dict[str, Any] | None = None,
    onboarding: dict[str, Any] | None = None,
) -> ProductWorkspaceResponse:
    workspace = _get_or_create_workspace(db=db, owner_user_id=owner_user_id, workspace_id=workspace_id, plan=plan, name=name)
    workspace.name = name
    workspace.plan = plan
    workspace.settings_json = json.dumps(_merge_defaults(DEFAULT_WORKSPACE_SETTINGS, settings))
    workspace.onboarding_json = json.dumps(_merge_defaults(DEFAULT_ONBOARDING, onboarding))
    _upsert_workspace_member(db=db, workspace=workspace, user_id=owner_user_id, role='owner')
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return _serialize_workspace(workspace)


def list_workspaces(db: Session, user_id: str) -> list[ProductWorkspaceResponse]:
    rows = (
        db.query(ProductWorkspace)
        .join(ProductWorkspaceMember, ProductWorkspaceMember.workspace_id == ProductWorkspace.id)
        .filter(ProductWorkspaceMember.user_id == user_id)
        .order_by(ProductWorkspace.updated_at.desc(), ProductWorkspace.created_at.desc())
        .all()
    )
    return [_serialize_workspace(workspace) for workspace in rows]


def add_workspace_member(db: Session, workspace_id: str, requester_user_id: str, user_id: str, role: str) -> ProductWorkspaceResponse | None:
    workspace = _workspace_for_admin(db=db, workspace_id=workspace_id, requester_user_id=requester_user_id)
    if workspace is None:
        return None
    _upsert_workspace_member(db=db, workspace=workspace, user_id=user_id, role=role)
    db.commit()
    db.refresh(workspace)
    return _serialize_workspace(workspace)


def invite_workspace_member(db: Session, workspace_id: str, requester_user_id: str, email: str, role: str) -> ProductWorkspaceInvitationResponse | None:
    workspace = _workspace_for_admin(db=db, workspace_id=workspace_id, requester_user_id=requester_user_id)
    if workspace is None:
        return None
    invitation = ProductWorkspaceInvitation(
        workspace_id=workspace.id,
        email=email.strip().lower(),
        role=role,
        invited_by_user_id=requester_user_id,
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    return _serialize_invitation(invitation)


def upsert_project(
    db: Session,
    user_id: str,
    project_id: str,
    name: str,
    plan: str,
    workspace_id: str | None = None,
    settings: dict[str, Any] | None = None,
    onboarding: dict[str, Any] | None = None,
) -> ProductProjectResponse | None:
    if workspace_id and _workspace_for_member(db=db, workspace_id=workspace_id, user_id=user_id) is None:
        return None

    project = _get_or_create_project(
        db=db,
        user_id=user_id,
        project_id=project_id,
        plan=plan,
        name=name,
        workspace_id=workspace_id,
    )
    project.name = name
    project.plan = plan
    if workspace_id:
        project.workspace_id = workspace_id
    project.settings_json = json.dumps(_merge_defaults(DEFAULT_WORKSPACE_SETTINGS, settings))
    project.onboarding_json = json.dumps(_merge_defaults(DEFAULT_ONBOARDING, onboarding))
    db.add(project)
    db.commit()
    db.refresh(project)
    return _serialize_project(project, run_count=_project_run_count(db, project.id))


def list_projects(db: Session, user_id: str) -> list[ProductProjectResponse]:
    rows = (
        db.query(ProductProject, func.count(ProductSavedRun.id))
        .outerjoin(ProductSavedRun, ProductSavedRun.project_id == ProductProject.id)
        .filter(ProductProject.user_id == user_id)
        .group_by(ProductProject.id)
        .order_by(ProductProject.updated_at.desc(), ProductProject.created_at.desc())
        .all()
    )
    return [_serialize_project(project, run_count=run_count) for project, run_count in rows]


def update_project_settings(db: Session, project_id: str, payload: ProductProjectSettingsRequest) -> ProductProjectResponse | None:
    project = (
        db.query(ProductProject)
        .filter(ProductProject.project_key == project_id, ProductProject.user_id == payload.user_id)
        .first()
    )
    if project is None:
        return None
    project.settings_json = json.dumps(_merge_defaults(DEFAULT_WORKSPACE_SETTINGS, payload.settings))
    project.onboarding_json = json.dumps(_merge_defaults(DEFAULT_ONBOARDING, payload.onboarding))
    db.add(project)
    db.commit()
    db.refresh(project)
    return _serialize_project(project, run_count=_project_run_count(db, project.id))


def save_run(db: Session, user_id: str, project_id: str, plan: str, report: dict[str, Any], transcript: str | None) -> SavedRunResponse:
    project = _get_or_create_project(db=db, user_id=user_id, project_id=project_id, plan=plan)
    created_at = datetime.now(UTC)
    seed = f'{user_id}:{project_id}:{created_at.isoformat()}:{report.get("run_id", "")}'
    previous_run = (
        db.query(ProductSavedRun)
        .filter(ProductSavedRun.project_id == project.id, ProductSavedRun.user_id == user_id)
        .order_by(ProductSavedRun.created_at.desc())
        .first()
    )
    previous_report = _load_json(previous_run.report_json, {}) if previous_run else None
    artifact_payload = _build_artifacts(report=report, transcript=transcript, previous_report=previous_report)
    saved = ProductSavedRun(
        id=hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16],
        user_id=user_id,
        project_id=project.id,
        plan=plan,
        report_json=json.dumps(report),
        transcript=transcript,
        artifact_json=json.dumps(artifact_payload),
        created_at=created_at,
    )
    project.plan = plan
    project.last_run_at = created_at
    db.add(project)
    db.add(saved)
    db.commit()
    db.refresh(saved)
    db.refresh(project)
    return _serialize_saved_run(saved, project)


def list_saved_runs(db: Session, user_id: str, project_id: str | None = None) -> list[SavedRunResponse]:
    query = (
        db.query(ProductSavedRun, ProductProject)
        .join(ProductProject, ProductProject.id == ProductSavedRun.project_id)
        .filter(ProductSavedRun.user_id == user_id)
    )
    if project_id is not None:
        query = query.filter(ProductProject.project_key == project_id)

    rows = query.order_by(ProductSavedRun.created_at.desc()).all()
    return [_serialize_saved_run(saved_run, project) for saved_run, project in rows]


def export_saved_run(db: Session, user_id: str, run_id: str) -> SavedRunExportResponse | None:
    row = (
        db.query(ProductSavedRun, ProductProject)
        .join(ProductProject, ProductProject.id == ProductSavedRun.project_id)
        .filter(ProductSavedRun.id == run_id, ProductSavedRun.user_id == user_id)
        .first()
    )
    if row is None:
        return None

    saved_run, project = row
    return SavedRunExportResponse(
        id=saved_run.id,
        filename=f'agentbench-{project.project_key}-{saved_run.id}.json',
        project_id=project.project_key,
        project_name=project.name,
        report=_load_json(saved_run.report_json, {}),
        artifacts=_load_json(saved_run.artifact_json, {}),
        transcript=saved_run.transcript,
        created_at=saved_run.created_at.replace(tzinfo=UTC).isoformat(),
    )


def judge_gate(plan: str, report: dict[str, Any], transcript: str | None) -> JudgeResponse:
    if plan == 'free':
        return JudgeResponse(
            status='blocked',
            required_plan='starter',
            credits=10,
            message='LLM judges are available on Starter and above. Free runs still use deterministic evidence checks.',
        )

    citations = _judge_citations(report, transcript)
    return JudgeResponse(
        status='ready',
        required_plan='starter',
        credits=10,
        message='LLM judge request accepted. Configure a judge provider key to execute model-backed review.',
        evidence_citations=citations,
    )


def reset_saved_runs_for_tests() -> None:
    with SessionLocal() as db:
        db.query(ProductSavedRun).delete()
        db.query(ProductProject).delete()
        db.query(ProductWorkspaceInvitation).delete()
        db.query(ProductWorkspaceMember).delete()
        db.query(ProductWorkspace).delete()
        db.commit()


def _firebase_auth_config() -> FirebaseAuthConfig:
    project_id = os.getenv('FIREBASE_PROJECT_ID') or os.getenv('NEXT_PUBLIC_FIREBASE_PROJECT_ID')
    api_key = os.getenv('FIREBASE_API_KEY') or os.getenv('NEXT_PUBLIC_FIREBASE_API_KEY')
    configured = bool(project_id and api_key)
    return FirebaseAuthConfig(
        enabled=True,
        project_id=project_id,
        api_key_configured=bool(api_key),
        providers=['email_link', 'google'],
        mode='configured' if configured else 'placeholder',
    )


def _judge_citations(report: dict[str, Any], transcript: str | None) -> list[str]:
    citations: list[str] = []
    evidence = report.get('evidence_spans') or report.get('evidence') or []
    if isinstance(evidence, list):
        for item in evidence[:4]:
            citations.append(str(item)[:180])
    if transcript and len(citations) < 2:
        citations.extend(line.strip()[:180] for line in transcript.splitlines() if line.strip())
    return citations[:4]


def _get_or_create_project(
    db: Session,
    user_id: str,
    project_id: str,
    plan: str,
    name: str | None = None,
    workspace_id: str | None = None,
) -> ProductProject:
    project = (
        db.query(ProductProject)
        .filter(ProductProject.user_id == user_id, ProductProject.project_key == project_id)
        .first()
    )
    if project is not None:
        if name:
            project.name = name
        return project

    project = ProductProject(
        user_id=user_id,
        workspace_id=workspace_id,
        project_key=project_id,
        name=name or _default_project_name(project_id),
        plan=plan,
        settings_json=json.dumps(DEFAULT_WORKSPACE_SETTINGS),
        onboarding_json=json.dumps(DEFAULT_ONBOARDING),
    )
    db.add(project)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        project = (
            db.query(ProductProject)
            .filter(ProductProject.user_id == user_id, ProductProject.project_key == project_id)
            .one()
        )
        if name:
            project.name = name
    return project


def _get_or_create_workspace(
    db: Session,
    owner_user_id: str,
    workspace_id: str,
    plan: str,
    name: str | None = None,
) -> ProductWorkspace:
    workspace = (
        db.query(ProductWorkspace)
        .filter(ProductWorkspace.owner_user_id == owner_user_id, ProductWorkspace.workspace_key == workspace_id)
        .first()
    )
    if workspace is not None:
        return workspace

    workspace = ProductWorkspace(
        owner_user_id=owner_user_id,
        workspace_key=workspace_id,
        name=name or _default_project_name(workspace_id),
        plan=plan,
        settings_json=json.dumps(DEFAULT_WORKSPACE_SETTINGS),
        onboarding_json=json.dumps(DEFAULT_ONBOARDING),
    )
    db.add(workspace)
    db.flush()
    return workspace


def _workspace_for_admin(db: Session, workspace_id: str, requester_user_id: str) -> ProductWorkspace | None:
    row = (
        db.query(ProductWorkspace, ProductWorkspaceMember)
        .join(ProductWorkspaceMember, ProductWorkspaceMember.workspace_id == ProductWorkspace.id)
        .filter(ProductWorkspace.id == workspace_id, ProductWorkspaceMember.user_id == requester_user_id)
        .first()
    )
    if row is None:
        return None
    workspace, member = row
    if member.role not in {'owner', 'admin'}:
        return None
    return workspace


def _workspace_for_member(db: Session, workspace_id: str, user_id: str) -> ProductWorkspace | None:
    row = (
        db.query(ProductWorkspace)
        .join(ProductWorkspaceMember, ProductWorkspaceMember.workspace_id == ProductWorkspace.id)
        .filter(ProductWorkspace.id == workspace_id, ProductWorkspaceMember.user_id == user_id)
        .first()
    )
    return row


def _upsert_workspace_member(db: Session, workspace: ProductWorkspace, user_id: str, role: str) -> ProductWorkspaceMember:
    member = (
        db.query(ProductWorkspaceMember)
        .filter(ProductWorkspaceMember.workspace_id == workspace.id, ProductWorkspaceMember.user_id == user_id)
        .first()
    )
    if member is None:
        member = ProductWorkspaceMember(workspace_id=workspace.id, user_id=user_id, role=role)
    else:
        member.role = role
    db.add(member)
    return member


def _project_run_count(db: Session, project_id: str) -> int:
    return (
        db.query(func.count(ProductSavedRun.id))
        .filter(ProductSavedRun.project_id == project_id)
        .scalar()
        or 0
    )


def _serialize_project(project: ProductProject, run_count: int) -> ProductProjectResponse:
    return ProductProjectResponse(
        id=project.id,
        user_id=project.user_id,
        workspace_id=project.workspace_id,
        project_id=project.project_key,
        name=project.name,
        plan=project.plan,  # type: ignore[arg-type]
        settings=_load_json(project.settings_json, {}),
        onboarding=_load_json(project.onboarding_json, {}),
        run_count=run_count,
        created_at=project.created_at.replace(tzinfo=UTC).isoformat(),
        updated_at=project.updated_at.replace(tzinfo=UTC).isoformat(),
        last_run_at=project.last_run_at.replace(tzinfo=UTC).isoformat() if project.last_run_at else None,
    )


def _serialize_workspace(workspace: ProductWorkspace) -> ProductWorkspaceResponse:
    return ProductWorkspaceResponse(
        id=workspace.id,
        owner_user_id=workspace.owner_user_id,
        workspace_id=workspace.workspace_key,
        name=workspace.name,
        plan=workspace.plan,  # type: ignore[arg-type]
        settings=_load_json(workspace.settings_json, {}),
        onboarding=_load_json(workspace.onboarding_json, {}),
        members=[_serialize_member(member) for member in sorted(workspace.members, key=lambda item: item.created_at)],
        invitations=[_serialize_invitation(invitation) for invitation in sorted(workspace.invitations, key=lambda item: item.created_at)],
        created_at=workspace.created_at.replace(tzinfo=UTC).isoformat(),
        updated_at=workspace.updated_at.replace(tzinfo=UTC).isoformat(),
    )


def _serialize_member(member: ProductWorkspaceMember) -> ProductWorkspaceMemberResponse:
    return ProductWorkspaceMemberResponse(
        id=member.id,
        user_id=member.user_id,
        role=member.role,  # type: ignore[arg-type]
        created_at=member.created_at.replace(tzinfo=UTC).isoformat(),
        updated_at=member.updated_at.replace(tzinfo=UTC).isoformat(),
    )


def _serialize_invitation(invitation: ProductWorkspaceInvitation) -> ProductWorkspaceInvitationResponse:
    return ProductWorkspaceInvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,  # type: ignore[arg-type]
        status=invitation.status,  # type: ignore[arg-type]
        invited_by_user_id=invitation.invited_by_user_id,
        created_at=invitation.created_at.replace(tzinfo=UTC).isoformat(),
    )


def _serialize_saved_run(saved_run: ProductSavedRun, project: ProductProject) -> SavedRunResponse:
    return SavedRunResponse(
        id=saved_run.id,
        user_id=saved_run.user_id,
        project_id=project.project_key,
        project_name=project.name,
        plan=saved_run.plan,  # type: ignore[arg-type]
        report=_load_json(saved_run.report_json, {}),
        artifacts=_load_json(saved_run.artifact_json, {}),
        transcript=saved_run.transcript,
        created_at=saved_run.created_at.replace(tzinfo=UTC).isoformat(),
    )


def _build_artifacts(report: dict[str, Any], transcript: str | None, previous_report: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'run_id': report.get('run_id'),
        'overall_score': report.get('overall_score'),
        'regression_delta': _regression_delta(report, previous_report),
        'evidence_spans': report.get('evidence_spans') or report.get('evidence') or [],
        'transcript_lines': len([line for line in (transcript or '').splitlines() if line.strip()]),
    }


def _regression_delta(report: dict[str, Any], previous_report: dict[str, Any] | None) -> dict[str, Any]:
    current_score = _numeric_score(_report_score(report))
    previous_score = _numeric_score(_report_score(previous_report)) if previous_report else None

    if previous_report is None or previous_score is None or current_score is None:
        return {
            'status': 'baseline',
            'previous_run_id': previous_report.get('run_id') if previous_report else None,
            'previous_overall_score': previous_score,
            'current_overall_score': current_score,
            'score_delta': None,
        }

    score_delta = current_score - previous_score
    if score_delta > 0:
        status = 'improved'
    elif score_delta < 0:
        status = 'regressed'
    else:
        status = 'unchanged'

    return {
        'status': status,
        'previous_run_id': previous_report.get('run_id'),
        'previous_overall_score': previous_score,
        'current_overall_score': current_score,
        'score_delta': score_delta,
    }


def _numeric_score(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _report_score(report: dict[str, Any]) -> Any:
    return report.get('overall_score') if 'overall_score' in report else report.get('score')


def _load_json(raw: str | None, fallback: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return fallback
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return fallback
    return loaded if isinstance(loaded, dict) else fallback


def _merge_defaults(defaults: dict[str, Any], values: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**defaults}
    if values:
        merged.update(values)
    return merged


def _default_project_name(project_id: str) -> str:
    return project_id.replace('-', ' ').replace('_', ' ').title() or 'Default Project'
