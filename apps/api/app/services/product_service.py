from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any

from app.schemas.product import (
    FirebaseAuthConfig,
    JudgeResponse,
    PricingPlan,
    ProductConfig,
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

_SAVED_RUNS: list[SavedRunResponse] = []


def product_config() -> ProductConfig:
    return ProductConfig(
        pricing=PRICING,
        usage_rules=USAGE_RULES,
        auth=_firebase_auth_config(),
        voice_status='gated',
        llm_judge_status='gated',
    )


def save_run(user_id: str, project_id: str, plan: str, report: dict[str, Any], transcript: str | None) -> SavedRunResponse:
    created_at = datetime.now(UTC).isoformat()
    seed = f'{user_id}:{project_id}:{created_at}:{report.get("run_id", "")}'
    saved = SavedRunResponse(
        id=hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16],
        user_id=user_id,
        project_id=project_id,
        plan=plan,  # type: ignore[arg-type]
        report=report,
        transcript=transcript,
        created_at=created_at,
    )
    _SAVED_RUNS.append(saved)
    return saved


def list_saved_runs(user_id: str, project_id: str | None = None) -> list[SavedRunResponse]:
    return [
        run for run in reversed(_SAVED_RUNS)
        if run.user_id == user_id and (project_id is None or run.project_id == project_id)
    ]


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
    _SAVED_RUNS.clear()


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
