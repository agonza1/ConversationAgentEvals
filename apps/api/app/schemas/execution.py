from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.run_provenance import ExecutionRunProvenance, ExecutorId, TesterId


ExecutionMode = Literal['text_callable', 'voice_fixture', 'pipecat_webrtc']
AudioTransportId = Literal['none', 'pipecat_small_webrtc', 'freeswitch_verto_sip']
TextCallableId = Literal['mock_agent', 'offline_acc_fixture', 'openai_codex', 'http_endpoint']
ConversationStatus = Literal['queued', 'running', 'completed', 'failed']
ExecutionRunStatus = Literal['queued', 'running', 'completed', 'needs_review', 'failed']


class ExecutionRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    suite_id: str = Field(default='call-center-voice-ai', min_length=1)
    scenario_ids: list[str] = Field(default_factory=list)
    mode: ExecutionMode = 'text_callable'
    iterations: int = Field(default=1, ge=1, le=20)
    max_exchanges: int = Field(default=3, ge=1, le=10)
    concurrent_sessions: int = Field(default=1, ge=1, le=4)
    user_id: str = Field(default='execution-user', min_length=1)
    project_id: str = Field(default='conversation-agent-evals', min_length=1)
    evaluate: bool = True
    text_callable: TextCallableId = 'mock_agent'
    voice_fixture_path: str | None = None
    audio_plan_path: str | None = None
    agent_id: str | None = None
    model_name: str | None = Field(default=None, min_length=1)
    tester_id: TesterId = 'scenario_simulator'
    tester_model_name: str | None = Field(default=None, min_length=1)
    executor_id: ExecutorId = 'local_async_runner'
    # Local Pipecat small WebRTC is the supported first slice; Verto SIP is rejected
    # until FreeSwitchVertoSipTransport is implemented.
    audio_transport: AudioTransportId = 'none'

    @model_validator(mode='after')
    def validate_audio_transport_for_mode(self) -> 'ExecutionRunCreateRequest':
        if self.mode == 'pipecat_webrtc':
            if 'tester_id' not in self.model_fields_set:
                self.tester_id = 'pipecat_tester'
            elif self.tester_id != 'pipecat_tester':
                raise ValueError('pipecat_webrtc mode requires tester_id=pipecat_tester')
            if 'executor_id' not in self.model_fields_set or self.executor_id == 'local_async_runner':
                self.executor_id = 'cae_local_audio_loop'
            elif self.executor_id != 'cae_local_audio_loop':
                raise ValueError('pipecat_webrtc mode requires executor_id=cae_local_audio_loop')
            if self.audio_transport in {'none', 'pipecat_small_webrtc'}:
                self.audio_transport = 'pipecat_small_webrtc'
            elif self.audio_transport == 'freeswitch_verto_sip':
                raise ValueError(
                    'audio_transport=freeswitch_verto_sip is deferred (FreeSWITCH Verto outbound SIP). '
                    'Use pipecat_small_webrtc for local execution audio hooks.'
                )
            else:
                raise ValueError('pipecat_webrtc mode requires audio_transport=pipecat_small_webrtc')
        elif self.audio_transport == 'freeswitch_verto_sip':
            raise ValueError(
                'audio_transport=freeswitch_verto_sip is deferred. '
                'Use none, or mode=pipecat_webrtc with pipecat_small_webrtc.'
            )
        elif self.mode == 'voice_fixture' and self.audio_transport != 'none':
            raise ValueError(
                'voice_fixture mode uses AccAudioFixtureScheduler only; set audio_transport=none '
                '(use mode=pipecat_webrtc for pipecat_small_webrtc hooks).'
            )
        elif self.mode == 'voice_fixture':
            if 'tester_id' not in self.model_fields_set:
                self.tester_id = 'fixture_replay'
            elif self.tester_id != 'fixture_replay':
                raise ValueError('voice_fixture mode requires tester_id=fixture_replay')
            if 'executor_id' not in self.model_fields_set:
                self.executor_id = 'evidence_replay'
            elif self.executor_id != 'evidence_replay':
                raise ValueError('voice_fixture mode requires executor_id=evidence_replay')
        elif self.mode == 'text_callable' and self.audio_transport != 'none':
            raise ValueError('text_callable mode does not stream execution audio; set audio_transport=none')
        elif self.text_callable == 'offline_acc_fixture':
            # Generated clients commonly serialize the generic text defaults.
            # Treat those placeholders like omitted fields for direct replay.
            if 'tester_id' not in self.model_fields_set or self.tester_id == 'scenario_simulator':
                self.tester_id = 'fixture_replay'
            elif self.tester_id != 'fixture_replay':
                raise ValueError('offline_acc_fixture replay requires tester_id=fixture_replay')
            if 'executor_id' not in self.model_fields_set or self.executor_id == 'local_async_runner':
                self.executor_id = 'evidence_replay'
            elif self.executor_id != 'evidence_replay':
                raise ValueError('offline_acc_fixture replay requires executor_id=evidence_replay')
        elif self.tester_id != 'scenario_simulator':
            raise ValueError('text_callable mode requires tester_id=scenario_simulator')
        elif self.executor_id not in {'local_async_runner', 'acc_browser_webrtc', 'acc_sip', 'acc_phone'}:
            raise ValueError('text_callable mode requires a text or ACC executor')
        return self


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


class LiveExecutionEvent(BaseModel):
    """Observable current-run evidence emitted before evaluation completes."""

    model_config = ConfigDict(extra='forbid')

    sequence: int = Field(ge=1)
    kind: Literal['message', 'audio']
    speaker: str
    text: str
    media_url: str | None = None
    mime_type: str | None = None
    direction: Literal['tester_to_target', 'target_to_tester'] | None = None
    llm_output: str | None = None
    asr_receipt: str | None = None
    frame_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


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
    live_events: list[LiveExecutionEvent] = Field(default_factory=list)
    transcript: str | None = None
    action_trace: list[dict[str, Any]] = Field(default_factory=list)
    final_state: dict[str, Any] = Field(default_factory=dict)
    latency_marks: list[dict[str, Any]] = Field(default_factory=list)
    metrics_summary: ConversationMetricsSummary | None = None
    timeline: list[TimelineEvent] = Field(default_factory=list)
    recording: dict[str, Any] | None = None
    vcon_export: dict[str, Any] | None = None
    vcon_export_summary: dict[str, Any] | None = None
    audio_session: dict[str, Any] | None = None
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
    model_name: str | None = None
    max_exchanges: int = Field(default=3, ge=1, le=10)
    tester_id: TesterId = 'scenario_simulator'
    tester_model_name: str | None = None
    executor_id: ExecutorId = 'local_async_runner'
    provenance: ExecutionRunProvenance | None = None
    # Immutable request and agent settings captured at queue time. Execution uses
    # this instead of re-reading the mutable agent registry in the background.
    execution_snapshot: dict[str, Any] | None = None
    progress: ExecutionRunProgress
    conversations: list[ConversationRecord] = Field(default_factory=list)
    inference_set_path: str | None = None
    run_snapshot_path: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None
