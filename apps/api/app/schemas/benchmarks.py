from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BenchmarkAction(BaseModel):
    id: str
    description: str


class BenchmarkFinalState(BaseModel):
    description: str
    assertions: dict[str, Any] = Field(default_factory=dict)


class BenchmarkScenario(BaseModel):
    id: str
    title: str
    prompt: str
    required_actions: list[BenchmarkAction] = Field(default_factory=list)
    forbidden_actions: list[BenchmarkAction] = Field(default_factory=list)
    expected_final_state: BenchmarkFinalState


class BenchmarkSuite(BaseModel):
    id: str
    title: str
    description: str
    scenarios: list[BenchmarkScenario]


class BenchmarkSuiteSummary(BaseModel):
    id: str
    title: str
    description: str
    scenario_count: int


class BenchmarkRunRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    suite_id: str | None = None
    suiteId: str | None = None
    scenario_id: str | None = None
    scenarioId: str | None = None
    transcript: str | None = None
    observed_actions: list[str] = Field(default_factory=list)
    conversation: str | dict[str, Any] | list[Any] | None = None
    call: str | dict[str, Any] | list[Any] | None = None
    group_call: str | dict[str, Any] | list[Any] | None = None
    groupCall: str | dict[str, Any] | list[Any] | None = None
    vcon: dict[str, Any] | None = None
    action_trace: str | dict[str, Any] | list[Any] | None = None
    final_state: dict[str, Any] | str | list[Any] = Field(default_factory=dict)
    agent_version: str | None = None
    agentVersion: str | None = None
    prompt_version: str | None = None
    promptVersion: str | None = None
    model_name: str | None = None
    modelName: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None
    attempt: int | None = None
    max_attempts: int | None = None
    maxAttempts: int | None = None
    retry_of_run_id: str | None = None
    retryOfRunId: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    resume_from_run_id: str | None = None
    resumeFromRunId: str | None = None

    @model_validator(mode='after')
    def evidence_must_not_be_blank(self) -> 'BenchmarkRunRequest':
        if (
            _has_text(self.transcript)
            or _has_text(self.conversation)
            or _has_text(self.call)
            or _has_text(self.group_call)
            or _has_text(self.groupCall)
            or _has_text(self.vcon)
            or _has_text(self.action_trace)
            or _has_text(self.final_state)
        ):
            if isinstance(self.transcript, str):
                self.transcript = self.transcript.strip()
            return self
        raise ValueError('Transcript, conversation, call, group_call, vcon, action_trace, or final_state evidence is required')


def _has_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        dialog = value.get('dialog')
        if isinstance(dialog, list):
            return any(_has_text(item.get('body') or item.get('text') or item.get('transcript')) for item in dialog if isinstance(item, dict))
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return False


class BenchmarkSimulationRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    suite_id: str | None = None
    suiteId: str | None = None
    scenario_id: str | None = None
    scenarioId: str | None = None
    agent_profile: str | None = None
    agentProfile: str | None = None
    include_failure: bool = False
    agent_version: str | None = None
    agentVersion: str | None = None
    prompt_version: str | None = None
    promptVersion: str | None = None
    model_name: str | None = None
    modelName: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None
    attempt: int | None = None
    max_attempts: int | None = None
    maxAttempts: int | None = None
    retry_of_run_id: str | None = None
    retryOfRunId: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    resume_from_run_id: str | None = None
    resumeFromRunId: str | None = None


class BenchmarkSuiteRunRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    suite_id: str | None = None
    suiteId: str | None = None
    scenario_evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)
    scenarioEvidence: dict[str, dict[str, Any]] = Field(default_factory=dict)
    agent_version: str | None = None
    agentVersion: str | None = None
    prompt_version: str | None = None
    promptVersion: str | None = None
    model_name: str | None = None
    modelName: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class BenchmarkSimulationResponse(BaseModel):
    suite_id: str
    scenario_id: str
    transcript: str
    action_trace: list[dict[str, Any]]
    final_state: dict[str, Any]
    run_metadata: dict[str, Any] = Field(default_factory=dict)
    benchmark_report: dict[str, Any]


class BenchmarkCheckResult(BaseModel):
    id: str
    description: str
    status: Literal['pass', 'fail']
    reason: str


class BenchmarkRunResponse(BaseModel):
    suite_id: str
    scenario_id: str
    status: Literal['pass', 'fail']
    score: int
    required_actions: list[BenchmarkCheckResult]
    forbidden_actions: list[BenchmarkCheckResult]
    final_state: BenchmarkCheckResult
