from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ExecutionMode = Literal['text_callable', 'voice_fixture']
ConversationStatus = Literal['queued', 'running', 'completed', 'failed']
ExecutionRunStatus = Literal['queued', 'running', 'completed', 'needs_review', 'failed']


class ExecutionRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    suite_id: str = Field(default='call-center-voice-ai', min_length=1)
    scenario_ids: list[str] = Field(default_factory=list)
    mode: ExecutionMode = 'text_callable'
    iterations: int = Field(default=1, ge=1, le=20)
    concurrent_sessions: int = Field(default=1, ge=1, le=4)
    user_id: str = Field(default='execution-user', min_length=1)
    project_id: str = Field(default='conversation-agent-evals', min_length=1)
    evaluate: bool = True
    text_callable: str = Field(default='mock_agent', min_length=1)
    voice_fixture_path: str | None = None
    audio_plan_path: str | None = None
    agent_id: str | None = None


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra='allow')

    turn_index: int
    speaker: str | None = None
    text: str | None = None
    act_id: str | None = None
    event_types: list[str] = Field(default_factory=list)
    latency_ms: float | None = None


class LatencyStats(BaseModel):
    model_config = ConfigDict(extra='forbid')

    count: int = 0
    avg_ms: float | None = None
    median_ms: float | None = None
    p90_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None
    outlier_count: int = 0


class ConversationMetricsSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')

    verdict: str | None = None
    score: float | None = None
    turn_count: int = 0
    latency: LatencyStats = Field(default_factory=LatencyStats)
    interruption_count: int = 0
    call_resolution_success: float = 0.0


class TimelineEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')

    t_ms: float | None = None
    label: str
    latency_ms: float | None = None
    kind: str = 'mark'


class ConversationRecord(BaseModel):
    """One inference_set.jsonl-shaped conversation row produced during Execute."""

    model_config = ConfigDict(extra='allow')

    conversation_id: str
    execution_run_id: str
    suite_id: str
    scenario_id: str
    scenario_title: str | None = None
    mode: ExecutionMode
    status: ConversationStatus
    iteration: int = 1
    turns: list[ConversationTurn] = Field(default_factory=list)
    transcript: str | None = None
    action_trace: list[dict[str, Any]] = Field(default_factory=list)
    final_state: dict[str, Any] = Field(default_factory=dict)
    latency_marks: list[dict[str, Any]] = Field(default_factory=list)
    metrics_summary: ConversationMetricsSummary | None = None
    timeline: list[TimelineEvent] = Field(default_factory=list)
    verdict: str | None = None
    score: float | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class ExecutionRunProgress(BaseModel):
    model_config = ConfigDict(extra='forbid')

    phase: str
    completed_conversations: int
    total_conversations: int
    percent: float
    active_conversation_id: str | None = None


class ExecutionRunRecord(BaseModel):
    model_config = ConfigDict(extra='allow')

    execution_run_id: str
    status: ExecutionRunStatus
    mode: ExecutionMode
    suite_id: str
    scenario_ids: list[str]
    user_id: str
    project_id: str
    agent_id: str | None = None
    agent_name: str | None = None
    progress: ExecutionRunProgress
    conversations: list[ConversationRecord] = Field(default_factory=list)
    inference_set_path: str | None = None
    run_snapshot_path: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None
