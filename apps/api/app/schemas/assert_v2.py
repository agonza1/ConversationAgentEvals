from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ArtifactKind = Literal[
    'transcript',
    'conversation',
    'vcon',
    'call_media',
    'action_trace',
    'final_state',
    'assert_bundle',
    'manifest',
    'report',
    'summary',
    'export',
    'other',
]
ArtifactReadiness = Literal['ready', 'pending', 'missing']
ExecutionMode = Literal['sync', 'async', 'batch']
TransportKind = Literal['http_sidecar']
RunStatus = Literal['queued', 'running', 'completed', 'failed', 'cancelled']
VerdictStatus = Literal['pass', 'fail', 'needs_review', 'error', 'queued', 'running']
FailureCategory = Literal[
    'task_completion',
    'required_action',
    'forbidden_action',
    'policy',
    'tool_use',
    'final_state',
    'conversation_quality',
    'runtime',
    'evidence',
    'unknown',
]
FailureSeverity = Literal['info', 'warning', 'error', 'critical']


class AssertSpecRef(BaseModel):
    model_config = ConfigDict(extra='forbid')

    spec_id: str = Field(..., min_length=1)
    spec_kind: Literal['scenario', 'suite']
    spec_version: str | None = None
    spec_hash: str | None = None
    assert_project: str | None = None
    assert_commit: str | None = None

    @model_validator(mode='after')
    def version_or_hash_is_required(self) -> 'AssertSpecRef':
        if _has_text(self.spec_version) or _has_text(self.spec_hash):
            return self
        raise ValueError('spec_version or spec_hash is required')


class AssertArtifactPointer(BaseModel):
    model_config = ConfigDict(extra='forbid')

    artifact_id: str = Field(..., min_length=1)
    kind: ArtifactKind
    role: Literal['input', 'output', 'derived'] = 'input'
    uri: str | None = None
    inline_data: Any | None = None
    mime_type: str | None = None
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    source: str | None = None
    readiness: ArtifactReadiness = 'ready'
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def location_or_payload_is_required(self) -> 'AssertArtifactPointer':
        if self.readiness == 'missing':
            return self
        if _has_text(self.uri) or self.inline_data is not None:
            return self
        raise ValueError('uri or inline_data is required unless readiness is missing')


class AssertEvidenceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    transcript: AssertArtifactPointer | None = None
    conversation: AssertArtifactPointer | None = None
    vcon: AssertArtifactPointer | None = None
    call_media: list[AssertArtifactPointer] = Field(default_factory=list)
    action_trace: AssertArtifactPointer | None = None
    final_state: AssertArtifactPointer | None = None
    assert_bundle: AssertArtifactPointer | None = None
    additional_artifacts: list[AssertArtifactPointer] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def evidence_must_exist(self) -> 'AssertEvidenceInput':
        if any(
            value is not None
            for value in (
                self.transcript,
                self.conversation,
                self.vcon,
                self.action_trace,
                self.final_state,
                self.assert_bundle,
            )
        ) or self.call_media or self.additional_artifacts:
            return self
        raise ValueError('at least one evidence artifact is required')


class AssertRetryPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')

    max_attempts: int = Field(default=1, ge=1)
    retryable_statuses: list[str] = Field(default_factory=lambda: ['error', 'failed'])


class AssertInvocationTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')

    transport: TransportKind = 'http_sidecar'
    environment: Literal['local', 'production']
    base_url: str = Field(..., min_length=1)
    package_name: str = Field(default='assert')
    entrypoint: str = Field(default='/v2/runs')
    timeout_seconds: int = Field(default=300, ge=1)


class AssertRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    execution_mode: ExecutionMode = 'async'
    invocation_target: AssertInvocationTarget
    worker_queue: str | None = None
    retry_policy: AssertRetryPolicy = Field(default_factory=AssertRetryPolicy)
    scenario_overrides: dict[str, Any] = Field(default_factory=dict)
    environment_labels: list[str] = Field(default_factory=list)


class PlatformRunMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')

    user_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    project_run_label: str | None = None
    root_run_id: str | None = None
    retry_parent_run_id: str | None = None
    resume_parent_run_id: str | None = None
    initiated_by: str = Field(default='api')
    notes: str | None = None
    labels: list[str] = Field(default_factory=list)
    retention_days: int = Field(default=90, ge=1)
    billing_tags: dict[str, str] = Field(default_factory=dict)
    quota_scope: str | None = None


class AssertVerdict(BaseModel):
    model_config = ConfigDict(extra='forbid')

    status: VerdictStatus
    score: float | None = None
    summary: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class AssertFailureItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    code: str = Field(..., min_length=1)
    category: FailureCategory
    severity: FailureSeverity
    summary: str = Field(..., min_length=1)
    expected: str | None = None
    observed: str | None = None
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssertResultManifest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    verdict: AssertVerdict
    failures: list[AssertFailureItem] = Field(default_factory=list)
    artifacts: list[AssertArtifactPointer] = Field(default_factory=list)
    raw_result: AssertArtifactPointer | None = None
    summary_artifacts: list[AssertArtifactPointer] = Field(default_factory=list)
    manifest_metadata: dict[str, Any] = Field(default_factory=dict)


class AuditArtifactView(BaseModel):
    model_config = ConfigDict(extra='forbid')

    ready_for_export: bool = False
    missing_artifact_ids: list[str] = Field(default_factory=list)
    artifacts: list[AssertArtifactPointer] = Field(default_factory=list)
    exports: list[AssertArtifactPointer] = Field(default_factory=list)


class AssertRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    spec_ref: AssertSpecRef
    evidence: AssertEvidenceInput
    runtime_config: AssertRuntimeConfig
    platform_metadata: PlatformRunMetadata


class AssertScenarioInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    scenario_ref: AssertSpecRef
    evidence: AssertEvidenceInput
    platform_metadata: PlatformRunMetadata


class AssertSuiteRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    spec_ref: AssertSpecRef
    scenarios: list[AssertScenarioInput] = Field(default_factory=list)
    runtime_config: AssertRuntimeConfig
    platform_metadata: PlatformRunMetadata

    @model_validator(mode='after')
    def suite_must_include_scenarios(self) -> 'AssertSuiteRunCreateRequest':
        if self.scenarios:
            return self
        raise ValueError('suite runs require at least one scenario input')


class PlatformRunRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    platform_run_id: str = Field(..., min_length=1)
    assert_run_id: str | None = None
    spec_ref: AssertSpecRef
    status: RunStatus
    created_at: str
    updated_at: str
    completed_at: str | None = None
    runtime_config: AssertRuntimeConfig
    platform_metadata: PlatformRunMetadata
    verdict: AssertVerdict | None = None
    failure_taxonomy: list[AssertFailureItem] = Field(default_factory=list)
    artifact_manifest: list[AssertArtifactPointer] = Field(default_factory=list)
    audit_artifacts: AuditArtifactView = Field(default_factory=AuditArtifactView)
    summary: dict[str, Any] = Field(default_factory=dict)


class PlatformSuiteScenarioRef(BaseModel):
    model_config = ConfigDict(extra='forbid')

    scenario_id: str = Field(..., min_length=1)
    platform_run_id: str = Field(..., min_length=1)
    assert_run_id: str | None = None
    status: RunStatus = 'queued'


class PlatformSuiteRunRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    platform_suite_run_id: str = Field(..., min_length=1)
    assert_run_id: str | None = None
    spec_ref: AssertSpecRef
    status: RunStatus
    created_at: str
    updated_at: str
    completed_at: str | None = None
    runtime_config: AssertRuntimeConfig
    platform_metadata: PlatformRunMetadata
    scenarios: list[PlatformSuiteScenarioRef] = Field(default_factory=list)
    verdict: AssertVerdict | None = None
    artifact_manifest: list[AssertArtifactPointer] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


def _has_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())
