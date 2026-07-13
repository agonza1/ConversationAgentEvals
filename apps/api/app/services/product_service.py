from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.entities import (
    ProductAuditEvent,
    ProductProject,
    ProductSavedRun,
    ProductWorkspace,
    ProductWorkspaceInvitation,
    ProductWorkspaceMember,
)
from app.services.benchmark_service import get_suite
from app.schemas.product import (
    CheckoutResponse,
    FirebaseAuthConfig,
    JudgeResponse,
    PricingPlan,
    ProductAuditEventResponse,
    ProductFailureCategorySummary,
    ProductScenarioRegressionSummary,
    ProductProjectRegressionSummary,
    ProductProjectContractArtifactSummary,
    ProductProjectExportResponse,
    ProductProjectScenarioCoverageSummary,
    ProductProjectSettingsRequest,
    ProductProjectVconExportSummary,
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
        pricing=_pricing_with_stripe_ids(),
        usage_rules=USAGE_RULES,
        auth=_firebase_auth_config(),
        voice_status='gated',
        llm_judge_status='gated',
    )


def _pricing_with_stripe_ids() -> list[PricingPlan]:
    price_ids = {
        'starter': os.getenv('STRIPE_STARTER_PRICE_ID') or None,
        'team': os.getenv('STRIPE_TEAM_PRICE_ID') or None,
    }
    return [plan.model_copy(update={'stripe_price_id': price_ids.get(plan.id)}) for plan in PRICING]


DEFAULT_WORKSPACE_SETTINGS = {
    'default_benchmark_suite': 'call-center-voice-ai',
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


def accept_workspace_invitation(
    db: Session,
    workspace_id: str,
    invitation_id: str,
    user_id: str,
    email: str,
) -> ProductWorkspaceResponse | None:
    invitation = (
        db.query(ProductWorkspaceInvitation)
        .filter(
            ProductWorkspaceInvitation.id == invitation_id,
            ProductWorkspaceInvitation.workspace_id == workspace_id,
            ProductWorkspaceInvitation.status == 'pending',
            ProductWorkspaceInvitation.email == email.strip().lower(),
        )
        .first()
    )
    if invitation is None:
        return None

    invitation.status = 'accepted'
    workspace = invitation.workspace
    _upsert_workspace_member(db=db, workspace=workspace, user_id=user_id, role=invitation.role)
    db.add(invitation)
    db.commit()
    db.refresh(workspace)
    return _serialize_workspace(workspace)


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
    workspace_ids = _member_workspace_ids(db=db, user_id=user_id)
    rows = (
        db.query(ProductProject, func.count(ProductSavedRun.id))
        .outerjoin(ProductSavedRun, ProductSavedRun.project_id == ProductProject.id)
        .filter(_visible_project_clause(user_id=user_id, workspace_ids=workspace_ids))
        .group_by(ProductProject.id)
        .order_by(ProductProject.updated_at.desc(), ProductProject.created_at.desc())
        .all()
    )
    return [_serialize_project(project, run_count=run_count) for project, run_count in rows]


def update_project_settings(db: Session, project_id: str, payload: ProductProjectSettingsRequest) -> ProductProjectResponse | None:
    project = _project_for_settings_editor(db=db, project_id=project_id, user_id=payload.user_id)
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
    previous_run = _previous_comparable_run(db=db, project=project, user_id=user_id, report=report)
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
    db.flush()
    _record_audit_event(
        db=db,
        user_id=project.user_id,
        actor_user_id=user_id,
        event_type='run.saved',
        project=project,
        payload={
            'run_id': saved.id,
            'logical_run_id': report.get('run_id'),
            'suite_id': _report_label(report, 'suite_id'),
            'scenario_id': _report_label(report, 'scenario_id'),
            'overall_score': _report_score(report),
        },
    )
    db.commit()
    db.refresh(saved)
    db.refresh(project)
    return _serialize_saved_run(saved, project)


def list_saved_runs(
    db: Session,
    user_id: str,
    project_id: str | None = None,
    suite_id: str | None = None,
    scenario_id: str | None = None,
) -> list[SavedRunResponse]:
    workspace_ids = _member_workspace_ids(db=db, user_id=user_id)
    query = (
        db.query(ProductSavedRun, ProductProject)
        .join(ProductProject, ProductProject.id == ProductSavedRun.project_id)
        .filter(_visible_project_clause(user_id=user_id, workspace_ids=workspace_ids))
    )
    if project_id is not None:
        query = query.filter(ProductProject.project_key == project_id)

    rows = query.order_by(ProductSavedRun.created_at.desc()).all()
    saved_runs = [_serialize_saved_run(saved_run, project) for saved_run, project in rows]
    if suite_id is not None:
        saved_runs = [run for run in saved_runs if _report_label(run.report, 'suite_id') == suite_id]
    if scenario_id is not None:
        saved_runs = [run for run in saved_runs if _report_label(run.report, 'scenario_id') == scenario_id]
    return saved_runs


def get_saved_run(db: Session, user_id: str, run_id: str) -> SavedRunResponse | None:
    workspace_ids = _member_workspace_ids(db=db, user_id=user_id)
    row = (
        db.query(ProductSavedRun, ProductProject)
        .join(ProductProject, ProductProject.id == ProductSavedRun.project_id)
        .filter(ProductSavedRun.id == run_id)
        .filter(_visible_project_clause(user_id=user_id, workspace_ids=workspace_ids))
        .first()
    )
    if row is None:
        return None

    saved_run, project = row
    return _serialize_saved_run(saved_run, project)


def _previous_comparable_run(
    db: Session,
    project: ProductProject,
    user_id: str,
    report: dict[str, Any],
) -> ProductSavedRun | None:
    suite_id = _report_label(report, 'suite_id')
    scenario_id = _report_label(report, 'scenario_id')
    candidates = (
        db.query(ProductSavedRun)
        .filter(ProductSavedRun.project_id == project.id, ProductSavedRun.user_id == user_id)
        .order_by(ProductSavedRun.created_at.desc())
        .all()
    )
    if not suite_id or not scenario_id:
        return candidates[0] if candidates else None

    for candidate in candidates:
        candidate_report = _load_json(candidate.report_json, {})
        if _report_label(candidate_report, 'suite_id') == suite_id and _report_label(candidate_report, 'scenario_id') == scenario_id:
            return candidate
    return None


def project_regression_summary(
    db: Session,
    user_id: str,
    project_id: str,
    suite_id: str | None = None,
    scenario_id: str | None = None,
) -> ProductProjectRegressionSummary | None:
    workspace_ids = _member_workspace_ids(db=db, user_id=user_id)
    project = (
        db.query(ProductProject)
        .filter(ProductProject.project_key == project_id)
        .filter(_visible_project_clause(user_id=user_id, workspace_ids=workspace_ids))
        .first()
    )
    if project is None:
        return None

    saved_runs = (
        db.query(ProductSavedRun)
        .filter(ProductSavedRun.project_id == project.id)
        .order_by(ProductSavedRun.created_at.desc())
        .all()
    )
    saved_runs = _filter_saved_runs_by_report_labels(saved_runs, suite_id=suite_id, scenario_id=scenario_id)
    scored_runs = [
        (saved_run, _numeric_score(_report_score(_load_json(saved_run.report_json, {}))))
        for saved_run in saved_runs
    ]
    scores = [score for _, score in scored_runs if score is not None]
    latest_report = _load_json(saved_runs[0].report_json, {}) if saved_runs else {}
    latest_score = scored_runs[0][1] if scored_runs else None
    previous_score = next((score for _, score in scored_runs[1:] if score is not None), None)
    latest_delta = latest_score - previous_score if latest_score is not None and previous_score is not None else None
    passing_runs = sum(1 for saved_run, score in scored_runs if _report_passed(_load_json(saved_run.report_json, {}), score))
    failing_runs = len(saved_runs) - passing_runs

    return ProductProjectRegressionSummary(
        user_id=user_id,
        project_id=project.project_key,
        run_count=len(saved_runs),
        latest_run_id=str(latest_report.get('run_id')) if latest_report.get('run_id') else None,
        latest_score=latest_score,
        previous_score=previous_score,
        latest_delta=latest_delta,
        latest_status=_delta_status(latest_score, previous_score),
        best_score=max(scores) if scores else None,
        worst_score=min(scores) if scores else None,
        average_score=round(sum(scores) / len(scores), 2) if scores else None,
        passing_runs=passing_runs,
        failing_runs=failing_runs,
        pass_rate=round((passing_runs / len(saved_runs)) * 100, 2) if saved_runs else None,
        scenario_summaries=_scenario_regression_summaries(saved_runs),
        failure_category_summary=_failure_category_summary(saved_runs),
    )


def _failure_category_summary(saved_runs: list[ProductSavedRun]) -> list[ProductFailureCategorySummary]:
    categories: dict[str, dict[str, Any]] = {}
    for saved_run in saved_runs:
        report = _load_json(saved_run.report_json, {})
        run_id = str(report.get('run_id')) if report.get('run_id') else saved_run.id
        for category in _report_failure_categories(report):
            bucket = categories.setdefault(category, {'count': 0, 'latest_run_id': run_id})
            bucket['count'] += 1

    return [
        ProductFailureCategorySummary(category=category, count=summary['count'], latest_run_id=summary['latest_run_id'])
        for category, summary in sorted(categories.items(), key=lambda item: (-item[1]['count'], item[0]))
    ]


def _report_failure_categories(report: dict[str, Any]) -> list[str]:
    raw_categories = report.get('failure_categories')
    categories: list[str] = []
    if isinstance(raw_categories, list):
        categories.extend(str(item).strip() for item in raw_categories if str(item).strip())

    root_cause = report.get('root_cause_tag')
    if isinstance(root_cause, str) and root_cause.strip():
        categories.append(root_cause.strip())

    return sorted(set(categories))


def _filter_saved_runs_by_report_labels(
    saved_runs: list[ProductSavedRun],
    *,
    suite_id: str | None = None,
    scenario_id: str | None = None,
) -> list[ProductSavedRun]:
    if suite_id is None and scenario_id is None:
        return saved_runs

    filtered = []
    for saved_run in saved_runs:
        report = _load_json(saved_run.report_json, {})
        if suite_id is not None and _report_label(report, 'suite_id') != suite_id:
            continue
        if scenario_id is not None and _report_label(report, 'scenario_id') != scenario_id:
            continue
        filtered.append(saved_run)
    return filtered


def _scenario_regression_summaries(saved_runs: list[ProductSavedRun]) -> list[ProductScenarioRegressionSummary]:
    grouped: dict[tuple[str | None, str], list[tuple[ProductSavedRun, dict[str, Any], int | float | None]]] = {}
    for saved_run in saved_runs:
        report = _load_json(saved_run.report_json, {})
        scenario_id = _report_label(report, 'scenario_id')
        if not scenario_id:
            continue
        suite_id = _report_label(report, 'suite_id')
        grouped.setdefault((suite_id, scenario_id), []).append((saved_run, report, _numeric_score(_report_score(report))))

    summaries: list[ProductScenarioRegressionSummary] = []
    for (suite_id, scenario_id), runs in sorted(grouped.items(), key=lambda item: ((item[0][0] or ''), item[0][1])):
        latest_run, latest_report, latest_score = runs[0]
        previous_score = next((score for _, _, score in runs[1:] if score is not None), None)
        latest_delta = latest_score - previous_score if latest_score is not None and previous_score is not None else None
        passing_runs = sum(1 for _, report, score in runs if _report_passed(report, score))
        failing_runs = len(runs) - passing_runs
        summaries.append(
            ProductScenarioRegressionSummary(
                suite_id=suite_id,
                scenario_id=scenario_id,
                run_count=len(runs),
                latest_run_id=str(latest_report.get('run_id')) if latest_report.get('run_id') else latest_run.id,
                latest_score=latest_score,
                previous_score=previous_score,
                latest_delta=latest_delta,
                latest_status=_delta_status(latest_score, previous_score),
                passing_runs=passing_runs,
                failing_runs=failing_runs,
                pass_rate=round((passing_runs / len(runs)) * 100, 2) if runs else None,
            )
        )
    return summaries


def export_saved_run(db: Session, user_id: str, run_id: str) -> SavedRunExportResponse | None:
    workspace_ids = _member_workspace_ids(db=db, user_id=user_id)
    row = (
        db.query(ProductSavedRun, ProductProject)
        .join(ProductProject, ProductProject.id == ProductSavedRun.project_id)
        .filter(ProductSavedRun.id == run_id)
        .filter(_visible_project_clause(user_id=user_id, workspace_ids=workspace_ids))
        .first()
    )
    if row is None:
        return None

    saved_run, project = row
    _record_audit_event(
        db=db,
        user_id=project.user_id,
        actor_user_id=user_id,
        event_type='run.exported',
        project=project,
        payload={'run_id': saved_run.id, 'export_type': 'single_run'},
    )
    db.commit()
    report = _load_saved_run_report(saved_run)
    return SavedRunExportResponse(
        id=saved_run.id,
        filename=f'agentbench-{project.project_key}-{saved_run.id}.json',
        project_id=project.project_key,
        project_name=project.name,
        firestore_path=_firestore_run_path(user_id=saved_run.user_id, project_key=project.project_key, run_id=saved_run.id),
        report=report,
        artifacts=_load_saved_run_artifacts(saved_run, report=report),
        transcript=saved_run.transcript,
        created_at=saved_run.created_at.replace(tzinfo=UTC).isoformat(),
    )


def export_project_runs(
    db: Session,
    user_id: str,
    project_id: str,
    suite_id: str | None = None,
    scenario_id: str | None = None,
) -> ProductProjectExportResponse | None:
    workspace_ids = _member_workspace_ids(db=db, user_id=user_id)
    project = (
        db.query(ProductProject)
        .filter(ProductProject.project_key == project_id)
        .filter(_visible_project_clause(user_id=user_id, workspace_ids=workspace_ids))
        .first()
    )
    if project is None:
        return None

    saved_runs = (
        db.query(ProductSavedRun)
        .filter(ProductSavedRun.project_id == project.id)
        .order_by(ProductSavedRun.created_at.desc())
        .all()
    )
    saved_runs = _filter_saved_runs_by_report_labels(saved_runs, suite_id=suite_id, scenario_id=scenario_id)
    summary = project_regression_summary(
        db=db,
        user_id=user_id,
        project_id=project_id,
        suite_id=suite_id,
        scenario_id=scenario_id,
    )
    if summary is None:
        return None

    runs = []
    for saved_run in saved_runs:
        report = _load_saved_run_report(saved_run)
        runs.append(
            SavedRunExportResponse(
                id=saved_run.id,
                filename=f'agentbench-{project.project_key}-{saved_run.id}.json',
                project_id=project.project_key,
                project_name=project.name,
                firestore_path=_firestore_run_path(user_id=saved_run.user_id, project_key=project.project_key, run_id=saved_run.id),
                report=report,
                artifacts=_load_saved_run_artifacts(saved_run, report=report),
                transcript=saved_run.transcript,
                created_at=saved_run.created_at.replace(tzinfo=UTC).isoformat(),
            )
        )

    filename_parts = ['agentbench', project.project_key]
    if suite_id:
        filename_parts.append(suite_id)
    if scenario_id:
        filename_parts.append(scenario_id)
    filename_parts.append('project-export')

    _record_audit_event(
        db=db,
        user_id=project.user_id,
        actor_user_id=user_id,
        event_type='project.exported',
        project=project,
        payload={
            'export_type': 'project_history',
            'run_count': len(runs),
            'suite_id': suite_id,
            'scenario_id': scenario_id,
        },
    )
    db.commit()

    return ProductProjectExportResponse(
        id=project.id,
        filename=f"{'-'.join(filename_parts)}.json",
        user_id=user_id,
        project_id=project.project_key,
        project_name=project.name,
        suite_id=suite_id,
        scenario_id=scenario_id,
        firestore_collection_path=_firestore_project_runs_path(user_id=user_id, project_key=project.project_key),
        run_count=len(runs),
        summary=summary,
        vcon_export_summary=_project_vcon_export_summary(runs),
        contract_artifact_summary=_project_contract_artifact_summary(runs),
        scenario_coverage_summary=_project_scenario_coverage_summary(runs, suite_id=suite_id),
        runs=runs,
        exported_at=datetime.now(UTC).isoformat(),
    )


def _project_contract_artifact_summary(runs: list[SavedRunExportResponse]) -> ProductProjectContractArtifactSummary:
    available_records = 0
    suite_hashes: set[str] = set()
    scenario_hashes: set[str] = set()
    for run in runs:
        summary = run.artifacts.get('contract_artifacts') if isinstance(run.artifacts, dict) else None
        if not isinstance(summary, dict) or not summary.get('available'):
            continue
        available_records += 1
        suite_hash = summary.get('suite_contract_manifest_sha256')
        scenario_hash = summary.get('scenario_contract_sha256')
        if isinstance(suite_hash, str) and suite_hash:
            suite_hashes.add(suite_hash)
        if isinstance(scenario_hash, str) and scenario_hash:
            scenario_hashes.add(scenario_hash)

    return ProductProjectContractArtifactSummary(
        available_records=available_records,
        missing_records=max(len(runs) - available_records, 0),
        total_runs=len(runs),
        suite_contract_manifest_sha256s=sorted(suite_hashes),
        scenario_contract_sha256s=sorted(scenario_hashes),
    )


def _project_scenario_coverage_summary(
    runs: list[SavedRunExportResponse],
    suite_id: str | None,
) -> ProductProjectScenarioCoverageSummary:
    covered_ids = sorted({
        str(run.report.get("scenario_id"))
        for run in runs
        if isinstance(run.report, dict) and run.report.get("scenario_id")
    })
    if not suite_id:
        return ProductProjectScenarioCoverageSummary(
            covered_scenario_count=len(covered_ids),
            covered_scenario_ids=covered_ids,
            covered_scenarios=[{"id": scenario_id, "title": scenario_id} for scenario_id in covered_ids],
            coverage_status="partial" if covered_ids else "empty",
        )

    suite = get_suite(suite_id)
    scenario_titles = {
        str(scenario.get("id")): str(scenario.get("title") or scenario.get("id"))
        for scenario in suite.get("scenarios", [])
        if scenario.get("id")
    } if suite else {}
    scenario_ids = list(scenario_titles.keys())
    if suite_id and not scenario_ids:
        return ProductProjectScenarioCoverageSummary(
            suite_id=suite_id,
            covered_scenario_count=len(covered_ids),
            covered_scenario_ids=covered_ids,
            covered_scenarios=[{"id": scenario_id, "title": scenario_id} for scenario_id in covered_ids],
            coverage_status="partial" if covered_ids else "empty",
        )

    covered_in_suite = [scenario_id for scenario_id in scenario_ids if scenario_id in covered_ids]
    out_of_suite_ids = [scenario_id for scenario_id in covered_ids if scenario_id not in scenario_titles]
    missing_ids = [scenario_id for scenario_id in scenario_ids if scenario_id not in covered_ids]
    coverage_percent = round((len(covered_in_suite) / len(scenario_ids)) * 100, 2) if scenario_ids else None
    recommended_next_scenario = missing_ids[0] if missing_ids else None

    return ProductProjectScenarioCoverageSummary(
        suite_id=suite_id,
        scenario_count=len(scenario_ids) if scenario_ids else None,
        covered_scenario_count=len(covered_in_suite),
        coverage_percent=coverage_percent,
        covered_scenario_ids=covered_in_suite,
        missing_scenario_ids=missing_ids,
        out_of_suite_scenario_ids=out_of_suite_ids,
        covered_scenarios=[{"id": scenario_id, "title": scenario_titles[scenario_id]} for scenario_id in covered_in_suite],
        missing_scenarios=[{"id": scenario_id, "title": scenario_titles[scenario_id]} for scenario_id in missing_ids],
        out_of_suite_scenarios=[{"id": scenario_id, "title": scenario_id} for scenario_id in out_of_suite_ids],
        recommended_next_scenario=(
            {"id": recommended_next_scenario, "title": scenario_titles[recommended_next_scenario]}
            if recommended_next_scenario else None
        ),
        coverage_status="complete" if scenario_ids and not missing_ids else "partial" if covered_in_suite else "empty",
    )

def _project_vcon_export_summary(runs: list[SavedRunExportResponse]) -> ProductProjectVconExportSummary:
    available_records = 0
    dialog_turns = 0
    analysis_records = 0
    for run in runs:
        summary = run.artifacts.get('vcon_export') if isinstance(run.artifacts, dict) else None
        if not isinstance(summary, dict) or not summary.get('available'):
            continue
        available_records += 1
        dialog_turns += _int_count(summary.get('dialog_turns'))
        analysis_records += _int_count(summary.get('analysis_count'))

    return ProductProjectVconExportSummary(
        available_records=available_records,
        missing_records=max(len(runs) - available_records, 0),
        total_runs=len(runs),
        dialog_turns=dialog_turns,
        analysis_records=analysis_records,
    )


def _int_count(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def list_audit_events(
    db: Session,
    user_id: str,
    project_id: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[ProductAuditEventResponse]:
    workspace_ids = _member_workspace_ids(db=db, user_id=user_id)
    visible_projects = (
        db.query(ProductProject)
        .filter(_visible_project_clause(user_id=user_id, workspace_ids=workspace_ids))
        .all()
    )
    if project_id is not None:
        visible_projects = [project for project in visible_projects if project.project_key == project_id]
        if not visible_projects:
            return []

    visible_project_ids = [project.id for project in visible_projects]
    query = db.query(ProductAuditEvent).filter(
        or_(
            ProductAuditEvent.user_id == user_id,
            ProductAuditEvent.actor_user_id == user_id,
            ProductAuditEvent.project_id.in_(visible_project_ids) if visible_project_ids else False,
            ProductAuditEvent.workspace_id.in_(workspace_ids) if workspace_ids else False,
        )
    )
    if event_type is not None:
        query = query.filter(ProductAuditEvent.event_type == event_type)

    rows = query.order_by(ProductAuditEvent.created_at.desc()).limit(limit).all()
    return [_serialize_audit_event(row) for row in rows]


def record_judge_request(
    db: Session,
    user_id: str,
    project_id: str | None,
    plan: str,
    status: str,
    credits: int,
) -> None:
    project = None
    if project_id:
        project = _get_or_create_project(db=db, user_id=user_id, project_id=project_id, plan=plan)
    _record_audit_event(
        db=db,
        user_id=user_id,
        actor_user_id=user_id,
        event_type='judge.requested',
        project=project,
        payload={'project_id': project_id, 'plan': plan, 'status': status, 'credits': credits},
    )
    db.commit()


def checkout_gate(
    *,
    plan: str,
    user_id: str,
    project_id: str,
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> CheckoutResponse:
    price_id = _stripe_price_id(plan)
    metadata = {
        'user_id': user_id,
        'project_id': project_id,
        'plan': plan,
    }
    if not price_id:
        return CheckoutResponse(
            status='blocked',
            plan=plan,  # type: ignore[arg-type]
            message='Stripe checkout is not configured for this plan yet.',
            metadata=metadata,
        )

    base_url = (os.getenv('STRIPE_CHECKOUT_BASE_URL') or '').rstrip('/')
    checkout_url = None
    if base_url:
        params = {
            'price_id': price_id,
            'client_reference_id': f'{user_id}:{project_id}',
        }
        if success_url:
            params['success_url'] = success_url
        if cancel_url:
            params['cancel_url'] = cancel_url
        checkout_url = f'{base_url}?{urlencode(params)}'

    if success_url:
        metadata['success_url'] = success_url
    if cancel_url:
        metadata['cancel_url'] = cancel_url

    return CheckoutResponse(
        status='ready',
        plan=plan,  # type: ignore[arg-type]
        stripe_price_id=price_id,
        checkout_url=checkout_url,
        message='Stripe price is configured and ready for checkout session creation.',
        metadata=metadata,
    )


def _stripe_price_id(plan: str) -> str | None:
    if plan == 'starter':
        return os.getenv('STRIPE_STARTER_PRICE_ID') or None
    if plan == 'team':
        return os.getenv('STRIPE_TEAM_PRICE_ID') or None
    return None


def judge_gate(plan: str, report: dict[str, Any], transcript: str | None) -> JudgeResponse:
    spend_control = _judge_spend_control()
    if plan == 'free':
        return JudgeResponse(
            status='blocked',
            required_plan='starter',
            credits=10,
            message='LLM judges are available on Starter and above. Free runs still use deterministic evidence checks.',
            spend_control=spend_control,
        )

    if not spend_control['within_budget']:
        return JudgeResponse(
            status='blocked',
            required_plan='starter',
            credits=10,
            message='LLM judge daily credit budget is exhausted. Increase the limit or wait for the next budget window.',
            spend_control=spend_control,
        )

    citations = _judge_citations(report, transcript)
    return JudgeResponse(
        status='ready',
        required_plan='starter',
        credits=10,
        message='LLM judge request accepted. Configure a judge provider key to execute model-backed review.',
        evidence_citations=citations,
        spend_control=spend_control,
    )


def reset_saved_runs_for_tests() -> None:
    with SessionLocal() as db:
        db.query(ProductAuditEvent).delete()
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


def _judge_spend_control() -> dict[str, Any]:
    daily_limit = _int_env('LLM_JUDGE_DAILY_CREDIT_LIMIT', 200)
    reserved_credits = _int_env('LLM_JUDGE_RESERVED_DAILY_CREDITS', 0)
    provider = (os.getenv('LLM_JUDGE_PROVIDER') or 'vertex').strip().lower()
    provider_configured = bool(
        os.getenv('LLM_JUDGE_API_KEY')
        or (provider == 'vertex' and (os.getenv('VERTEX_PROJECT_ID') or os.getenv('GOOGLE_CLOUD_PROJECT')))
    )
    remaining = max(daily_limit - reserved_credits, 0)
    return {
        'estimated_credits': 10,
        'daily_credit_limit': daily_limit,
        'reserved_daily_credits': reserved_credits,
        'remaining_daily_credits': remaining,
        'provider': provider,
        'provider_configured': provider_configured,
        'within_budget': remaining >= 10,
    }


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


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


def _project_for_settings_editor(db: Session, project_id: str, user_id: str) -> ProductProject | None:
    project = db.query(ProductProject).filter(ProductProject.project_key == project_id).first()
    if project is None:
        return None
    if project.user_id == user_id:
        return project
    if not project.workspace_id:
        return None

    member = (
        db.query(ProductWorkspaceMember)
        .filter(ProductWorkspaceMember.workspace_id == project.workspace_id, ProductWorkspaceMember.user_id == user_id)
        .first()
    )
    if member is None or member.role not in {'owner', 'admin', 'editor'}:
        return None
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


def _member_workspace_ids(db: Session, user_id: str) -> list[str]:
    return [
        str(workspace_id)
        for (workspace_id,) in db.query(ProductWorkspaceMember.workspace_id)
        .filter(ProductWorkspaceMember.user_id == user_id)
        .all()
    ]


def _visible_project_clause(user_id: str, workspace_ids: list[str]):
    if not workspace_ids:
        return ProductProject.user_id == user_id
    return or_(ProductProject.user_id == user_id, ProductProject.workspace_id.in_(workspace_ids))


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



def _firestore_project_runs_path(user_id: str, project_key: str) -> str:
    return f'users/{_firestore_segment(user_id)}/projects/{_firestore_segment(project_key)}/runs'


def _firestore_run_path(user_id: str, project_key: str, run_id: str) -> str:
    return f'{_firestore_project_runs_path(user_id=user_id, project_key=project_key)}/{_firestore_segment(run_id)}'


def _firestore_segment(value: str) -> str:
    return value.strip().replace('/', '_') or 'default'

def _serialize_audit_event(event: ProductAuditEvent) -> ProductAuditEventResponse:
    return ProductAuditEventResponse(
        id=event.id,
        user_id=event.user_id,
        actor_user_id=event.actor_user_id,
        project_id=event.project.project_key if event.project else None,
        workspace_id=event.workspace_id,
        event_type=event.event_type,
        payload=_load_json(event.payload_json, {}),
        created_at=event.created_at.replace(tzinfo=UTC).isoformat(),
    )


def _record_audit_event(
    db: Session,
    user_id: str,
    actor_user_id: str,
    event_type: str,
    project: ProductProject | None = None,
    workspace: ProductWorkspace | None = None,
    payload: dict[str, Any] | None = None,
) -> ProductAuditEvent:
    event = ProductAuditEvent(
        user_id=user_id,
        actor_user_id=actor_user_id,
        project_id=project.id if project else None,
        workspace_id=workspace.id if workspace else (project.workspace_id if project else None),
        event_type=event_type,
        payload_json=json.dumps({key: value for key, value in (payload or {}).items() if value is not None}),
    )
    db.add(event)
    return event


def _serialize_saved_run(saved_run: ProductSavedRun, project: ProductProject) -> SavedRunResponse:
    report = _load_saved_run_report(saved_run)
    return SavedRunResponse(
        id=saved_run.id,
        user_id=saved_run.user_id,
        project_id=project.project_key,
        project_name=project.name,
        firestore_path=_firestore_run_path(user_id=saved_run.user_id, project_key=project.project_key, run_id=saved_run.id),
        plan=saved_run.plan,  # type: ignore[arg-type]
        report=report,
        artifacts=_load_saved_run_artifacts(saved_run, report=report),
        transcript=saved_run.transcript,
        created_at=saved_run.created_at.replace(tzinfo=UTC).isoformat(),
    )


def _load_saved_run_report(saved_run: ProductSavedRun) -> dict[str, Any]:
    return _load_json(saved_run.report_json, {})


def _load_saved_run_artifacts(saved_run: ProductSavedRun, *, report: dict[str, Any]) -> dict[str, Any]:
    artifacts = _load_json(saved_run.artifact_json, {})
    audit_artifacts = artifacts.get('audit_artifacts')
    if isinstance(audit_artifacts, dict):
        evaluator_version = audit_artifacts.get('evaluator_version')
        if evaluator_version is None:
            evidence_audit_summary = report.get('evidence_audit_summary')
            if isinstance(evidence_audit_summary, dict):
                evaluator_version = evidence_audit_summary.get('evaluator_version')
        artifacts = {
            **artifacts,
            'audit_artifacts': {
                **audit_artifacts,
                **_audit_artifact_policy(evaluator_version),
            },
        }
    elif 'evidence_audit_summary' in report:
        artifacts = {
            **artifacts,
            'audit_artifacts': _audit_artifact_summary(report.get('evidence_audit_summary')),
        }
    return artifacts


def _build_artifacts(report: dict[str, Any], transcript: str | None, previous_report: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'run_id': report.get('run_id'),
        'overall_score': report.get('overall_score'),
        'regression_delta': _regression_delta(report, previous_report),
        'evidence_spans': report.get('evidence_spans') or report.get('evidence') or [],
        'evidence_citations': report.get('evidence_citations') or [],
        'audit_artifacts': _audit_artifact_summary(report.get('evidence_audit_summary')),
        'contract_artifacts': _contract_artifact_summary(report),
        'vcon_export': _vcon_export_summary(report.get('vcon_export')),
        'transcript_lines': len([line for line in (transcript or '').splitlines() if line.strip()]),
    }


def _audit_artifact_summary(evidence_audit_summary: Any) -> dict[str, Any]:
    if not isinstance(evidence_audit_summary, dict):
        return {
            'available': False,
            'ready_for_export': False,
            'artifact_types': [],
            'missing': [],
            'evaluator_version': None,
        }

    export_readiness = evidence_audit_summary.get('export_readiness')
    if not isinstance(export_readiness, dict):
        export_readiness = {}
    artifact_types = evidence_audit_summary.get('input_artifact_types')
    missing = export_readiness.get('missing')

    evaluator_version = evidence_audit_summary.get('evaluator_version')
    return {
        'available': True,
        'ready_for_export': bool(export_readiness.get('ready')),
        'artifact_types': artifact_types if isinstance(artifact_types, list) else [],
        'missing': missing if isinstance(missing, list) else [],
        'evaluator_version': evaluator_version,
        **_audit_artifact_policy(evaluator_version),
    }


def _audit_artifact_policy(evaluator_version: Any) -> dict[str, Any]:
    if isinstance(evaluator_version, str) and evaluator_version.startswith('assert-'):
        return {'classification': 'assert', 'active_evaluator_input': True}
    return {'classification': 'unsupported', 'active_evaluator_input': False}


def _contract_artifact_summary(report: dict[str, Any]) -> dict[str, Any]:
    suite_contract_manifest_sha256 = report.get('suite_contract_manifest_sha256')
    scenario_contract_sha256 = report.get('scenario_contract_sha256')
    return {
        'available': bool(suite_contract_manifest_sha256 or scenario_contract_sha256),
        'suite_contract_manifest_sha256': suite_contract_manifest_sha256 if isinstance(suite_contract_manifest_sha256, str) else None,
        'scenario_contract_sha256': scenario_contract_sha256 if isinstance(scenario_contract_sha256, str) else None,
    }


def _vcon_export_summary(vcon_export: Any) -> dict[str, Any]:
    if not isinstance(vcon_export, dict):
        return {'available': False, 'dialog_turns': 0, 'analysis_count': 0, 'source_format': None}

    dialog = vcon_export.get('dialog')
    analysis = vcon_export.get('analysis')
    return {
        'available': True,
        'dialog_turns': len(dialog) if isinstance(dialog, list) else 0,
        'analysis_count': len(analysis) if isinstance(analysis, list) else 0,
        'source_format': vcon_export.get('source_format'),
        'appended_analysis_type': vcon_export.get('appended_analysis_type'),
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


def _delta_status(current_score: int | float | None, previous_score: int | float | None) -> str:
    if current_score is None:
        return 'none'
    if previous_score is None:
        return 'baseline'
    if current_score > previous_score:
        return 'improved'
    if current_score < previous_score:
        return 'regressed'
    return 'unchanged'


def _report_label(report: dict[str, Any], key: str) -> str | None:
    value = report.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _report_passed(report: dict[str, Any], score: int | float | None) -> bool:
    verdict = report.get('verdict') or report.get('status') or report.get('overall')
    if isinstance(verdict, str):
        normalized = verdict.strip().lower()
        if normalized in {'pass', 'passed'}:
            return True
        if normalized in {'fail', 'failed', 'needs_review', 'blocked'}:
            return False
    return score is not None and score >= 75


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
