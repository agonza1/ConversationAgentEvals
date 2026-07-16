from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ExecutionMode = Literal['text_callable', 'voice_fixture', 'pipecat_webrtc']
AudioTransportId = Literal['none', 'pipecat_small_webrtc', 'freeswitch_verto_sip']
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
    # Local Pipecat small WebRTC is the supported first slice; Verto SIP is rejected
    # until FreeSwitchVertoSipTransport is implemented.
    audio_transport: AudioTransportId = 'none'

    @model_validator(mode='after')
    def validate_audio_transport_for_mode(self) -> 'ExecutionRunCreateRequest':
        if self.mode == 'pipecat_webrtc':
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
        elif self.mode == 'text_callable' and self.audio_transport != 'none':
            raise ValueError('text_callable mode does not stream execution audio; set audio_transport=none')
        return self


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra='allow')

    turn_index: int
    speaker: str | None = None
    text: str | None = None
    act_id: str | None = None
    event_types: list[str] = Field(default_factory=list)
    latency_ms: float | None = None


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
    progress: ExecutionRunProgress
    conversations: list[ConversationRecord] = Field(default_factory=list)
    inference_set_path: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None
