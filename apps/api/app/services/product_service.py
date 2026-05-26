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
from app.models.entities import ProductProject, ProductSavedRun
from app.schemas.product import (
    FirebaseAuthConfig,
    JudgeResponse,
    PricingPlan,
    ProductProjectResponse,
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


def upsert_project(db: Session, user_id: str, project_id: str, name: str, plan: str) -> ProductProjectResponse:
    project = _get_or_create_project(db=db, user_id=user_id, project_id=project_id, plan=plan, name=name)
    project.name = name
    project.plan = plan
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


def save_run(db: Session, user_id: str, project_id: str, plan: str, report: dict[str, Any], transcript: str | None) -> SavedRunResponse:
    project = _get_or_create_project(db=db, user_id=user_id, project_id=project_id, plan=plan)
    created_at = datetime.now(UTC)
    seed = f'{user_id}:{project_id}:{created_at.isoformat()}:{report.get("run_id", "")}'
    artifact_payload = _build_artifacts(report=report, transcript=transcript)
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
        project_key=project_id,
        name=name or _default_project_name(project_id),
        plan=plan,
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
        project_id=project.project_key,
        name=project.name,
        plan=project.plan,  # type: ignore[arg-type]
        run_count=run_count,
        created_at=project.created_at.replace(tzinfo=UTC).isoformat(),
        updated_at=project.updated_at.replace(tzinfo=UTC).isoformat(),
        last_run_at=project.last_run_at.replace(tzinfo=UTC).isoformat() if project.last_run_at else None,
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


def _build_artifacts(report: dict[str, Any], transcript: str | None) -> dict[str, Any]:
    return {
        'run_id': report.get('run_id'),
        'overall_score': report.get('overall_score'),
        'evidence_spans': report.get('evidence_spans') or report.get('evidence') or [],
        'transcript_lines': len([line for line in (transcript or '').splitlines() if line.strip()]),
    }


def _load_json(raw: str | None, fallback: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return fallback
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return fallback
    return loaded if isinstance(loaded, dict) else fallback


def _default_project_name(project_id: str) -> str:
    return project_id.replace('-', ' ').replace('_', ' ').title() or 'Default Project'
