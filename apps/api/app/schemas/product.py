from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PlanId = Literal['free', 'starter', 'team', 'business']


class PricingPlan(BaseModel):
    id: PlanId
    name: str
    price_label: str
    seats: str
    included_credits: int | None = None
    cta: str
    features: list[str]


class UsageRule(BaseModel):
    id: str
    label: str
    credits: int
    gated_plan: PlanId | None = None


class FirebaseAuthConfig(BaseModel):
    enabled: bool
    project_id: str | None = None
    api_key_configured: bool = False
    providers: list[str] = Field(default_factory=list)
    mode: Literal['configured', 'placeholder'] = 'placeholder'


class ProductConfig(BaseModel):
    pricing: list[PricingPlan]
    usage_rules: list[UsageRule]
    auth: FirebaseAuthConfig
    voice_status: Literal['planned', 'gated', 'enabled']
    llm_judge_status: Literal['planned', 'gated', 'enabled']


class SavedRunRequest(BaseModel):
    user_id: str = Field(min_length=1)
    project_id: str = Field(default='default')
    plan: PlanId = 'free'
    report: dict[str, Any]
    transcript: str | None = None


class SavedRunResponse(BaseModel):
    id: str
    user_id: str
    project_id: str
    plan: PlanId
    report: dict[str, Any]
    transcript: str | None = None
    created_at: str


class JudgeRequest(BaseModel):
    plan: PlanId = 'free'
    report: dict[str, Any] = Field(default_factory=dict)
    transcript: str | None = None


class JudgeResponse(BaseModel):
    status: Literal['blocked', 'ready']
    required_plan: PlanId
    credits: int
    message: str
    evidence_citations: list[str] = Field(default_factory=list)
