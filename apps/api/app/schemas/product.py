from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PlanId = Literal['free', 'starter', 'team', 'business']
WorkspaceRole = Literal['owner', 'admin', 'editor', 'viewer']


class PricingPlan(BaseModel):
    id: PlanId
    name: str
    price_label: str
    stripe_price_id: str | None = None
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


class CheckoutRequest(BaseModel):
    plan: Literal['starter', 'team']
    user_id: str = Field(min_length=1)
    project_id: str = Field(default='default')
    success_url: str | None = None
    cancel_url: str | None = None


class CheckoutResponse(BaseModel):
    status: Literal['ready', 'blocked']
    plan: Literal['starter', 'team']
    stripe_price_id: str | None = None
    checkout_url: str | None = None
    mode: Literal['subscription'] = 'subscription'
    message: str
    metadata: dict[str, str] = Field(default_factory=dict)


class ProductProjectRequest(BaseModel):
    user_id: str = Field(min_length=1)
    workspace_id: str | None = None
    project_id: str = Field(default='default')
    name: str = Field(default='Default Project', min_length=1, max_length=80)
    plan: PlanId = 'free'
    settings: dict[str, Any] = Field(default_factory=dict)
    onboarding: dict[str, Any] = Field(default_factory=dict)


class ProductProjectResponse(BaseModel):
    id: str
    user_id: str
    workspace_id: str | None = None
    project_id: str
    name: str
    plan: PlanId
    settings: dict[str, Any] = Field(default_factory=dict)
    onboarding: dict[str, Any] = Field(default_factory=dict)
    run_count: int = 0
    created_at: str
    updated_at: str
    last_run_at: str | None = None


class ProductScenarioRegressionSummary(BaseModel):
    suite_id: str | None = None
    scenario_id: str
    run_count: int
    latest_run_id: str | None = None
    latest_score: int | float | None = None
    previous_score: int | float | None = None
    latest_delta: int | float | None = None
    latest_status: Literal['baseline', 'improved', 'regressed', 'unchanged', 'none']
    passing_runs: int = 0
    failing_runs: int = 0
    pass_rate: float | None = None


class ProductFailureCategorySummary(BaseModel):
    category: str
    count: int
    latest_run_id: str | None = None


class ProductProjectRegressionSummary(BaseModel):
    user_id: str
    project_id: str
    run_count: int
    latest_run_id: str | None = None
    latest_score: int | float | None = None
    previous_score: int | float | None = None
    latest_delta: int | float | None = None
    latest_status: Literal['baseline', 'improved', 'regressed', 'unchanged', 'none']
    best_score: int | float | None = None
    worst_score: int | float | None = None
    average_score: float | None = None
    passing_runs: int = 0
    failing_runs: int = 0
    pass_rate: float | None = None
    scenario_summaries: list[ProductScenarioRegressionSummary] = Field(default_factory=list)
    failure_category_summary: list[ProductFailureCategorySummary] = Field(default_factory=list)


class ProductProjectVconExportSummary(BaseModel):
    available_records: int = 0
    missing_records: int = 0
    total_runs: int = 0
    dialog_turns: int = 0
    analysis_records: int = 0


class ProductProjectContractArtifactSummary(BaseModel):
    available_records: int = 0
    missing_records: int = 0
    total_runs: int = 0
    suite_contract_manifest_sha256s: list[str] = Field(default_factory=list)
    scenario_contract_sha256s: list[str] = Field(default_factory=list)


class ProductScenarioCoverageItem(BaseModel):
    id: str
    title: str


class ProductProjectScenarioCoverageSummary(BaseModel):
    suite_id: str | None = None
    scenario_count: int | None = None
    covered_scenario_count: int = 0
    coverage_percent: float | None = None
    covered_scenario_ids: list[str] = Field(default_factory=list)
    missing_scenario_ids: list[str] = Field(default_factory=list)
    out_of_suite_scenario_ids: list[str] = Field(default_factory=list)
    covered_scenarios: list[ProductScenarioCoverageItem] = Field(default_factory=list)
    missing_scenarios: list[ProductScenarioCoverageItem] = Field(default_factory=list)
    out_of_suite_scenarios: list[ProductScenarioCoverageItem] = Field(default_factory=list)
    recommended_next_scenario: ProductScenarioCoverageItem | None = None
    coverage_status: Literal['empty', 'partial', 'complete'] = 'empty'


class ProductWorkspaceRequest(BaseModel):
    owner_user_id: str = Field(min_length=1)
    workspace_id: str = Field(default='default')
    name: str = Field(default='Default Workspace', min_length=1, max_length=80)
    plan: PlanId = 'free'
    settings: dict[str, Any] = Field(default_factory=dict)
    onboarding: dict[str, Any] = Field(default_factory=dict)


class ProductWorkspaceMemberResponse(BaseModel):
    id: str
    user_id: str
    role: WorkspaceRole
    created_at: str
    updated_at: str


class ProductWorkspaceInvitationResponse(BaseModel):
    id: str
    email: str
    role: WorkspaceRole
    status: Literal['pending', 'accepted', 'revoked']
    invited_by_user_id: str
    created_at: str


class ProductWorkspaceResponse(BaseModel):
    id: str
    owner_user_id: str
    workspace_id: str
    name: str
    plan: PlanId
    settings: dict[str, Any] = Field(default_factory=dict)
    onboarding: dict[str, Any] = Field(default_factory=dict)
    members: list[ProductWorkspaceMemberResponse] = Field(default_factory=list)
    invitations: list[ProductWorkspaceInvitationResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ProductWorkspaceMemberRequest(BaseModel):
    requester_user_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: WorkspaceRole = 'viewer'


class ProductWorkspaceInvitationRequest(BaseModel):
    requester_user_id: str = Field(min_length=1)
    email: str = Field(min_length=3)
    role: WorkspaceRole = 'viewer'


class ProductWorkspaceInvitationAcceptRequest(BaseModel):
    user_id: str = Field(min_length=1)
    email: str = Field(min_length=3)


class ProductProjectSettingsRequest(BaseModel):
    user_id: str = Field(min_length=1)
    settings: dict[str, Any] = Field(default_factory=dict)
    onboarding: dict[str, Any] = Field(default_factory=dict)


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
    project_name: str
    firestore_path: str
    plan: PlanId
    report: dict[str, Any]
    artifacts: dict[str, Any] = Field(default_factory=dict)
    transcript: str | None = None
    created_at: str


class SavedRunExportResponse(BaseModel):
    id: str
    filename: str
    project_id: str
    project_name: str
    firestore_path: str
    report: dict[str, Any]
    artifacts: dict[str, Any] = Field(default_factory=dict)
    transcript: str | None = None
    created_at: str


class ProductProjectExportResponse(BaseModel):
    id: str
    filename: str
    user_id: str
    project_id: str
    project_name: str
    suite_id: str | None = None
    scenario_id: str | None = None
    firestore_collection_path: str
    run_count: int
    summary: ProductProjectRegressionSummary
    vcon_export_summary: ProductProjectVconExportSummary = Field(default_factory=ProductProjectVconExportSummary)
    contract_artifact_summary: ProductProjectContractArtifactSummary = Field(default_factory=ProductProjectContractArtifactSummary)
    scenario_coverage_summary: ProductProjectScenarioCoverageSummary = Field(default_factory=ProductProjectScenarioCoverageSummary)
    runs: list[SavedRunExportResponse] = Field(default_factory=list)
    exported_at: str


class ProductAuditEventResponse(BaseModel):
    id: str
    user_id: str
    actor_user_id: str
    project_id: str | None = None
    workspace_id: str | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class JudgeRequest(BaseModel):
    plan: PlanId = 'free'
    report: dict[str, Any] = Field(default_factory=dict)
    transcript: str | None = None
    user_id: str | None = None
    project_id: str | None = None


class JudgeStructuredResult(BaseModel):
    agrees: bool | None = None
    rationale: str | None = None
    next_action: str | None = None
    raw_output: str | None = None


class JudgeResponse(BaseModel):
    status: Literal['blocked', 'ready']
    required_plan: PlanId
    credits: int
    message: str
    evidence_citations: list[str] = Field(default_factory=list)
    spend_control: dict[str, Any] = Field(default_factory=dict)
    judge_output: str | None = None
    judge_result: JudgeStructuredResult | None = None
    provider: str | None = None
    model: str | None = None
    prompt_preview: str | None = None
    latency_ms: int | None = None
    block_reason: Literal['provider', 'budget', 'provider_error', 'evidence'] | None = None
