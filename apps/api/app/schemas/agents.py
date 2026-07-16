from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AgentChannel = Literal['text', 'voice']
AgentTarget = Literal['mock_agent', 'openai_codex', 'offline_acc_fixture', 'voice_fixture']

_TEXT_AGENT_TARGETS = frozenset({'mock_agent', 'openai_codex', 'offline_acc_fixture'})
_VOICE_AGENT_TARGETS = frozenset({'voice_fixture'})


def validate_agent_channel_target(channel: AgentChannel, target: AgentTarget) -> None:
    allowed_targets = _VOICE_AGENT_TARGETS if channel == 'voice' else _TEXT_AGENT_TARGETS
    if target not in allowed_targets:
        allowed = ', '.join(f'"{item}"' for item in sorted(allowed_targets))
        raise ValueError(f'Channel "{channel}" requires one of: {allowed}.')


class AgentMetadata(BaseModel):
    model_config = ConfigDict(extra='allow', protected_namespaces=())

    model_name: str | None = None
    prompt_version: str | None = None


class AgentRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    name: str
    channel: AgentChannel
    target: AgentTarget
    description: str | None = None
    metadata: AgentMetadata = Field(default_factory=AgentMetadata)
    created_at: str
    updated_at: str

    @model_validator(mode='after')
    def validate_channel_target_pair(self):
        validate_agent_channel_target(self.channel, self.target)
        return self


class AgentCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str | None = Field(default=None, min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    channel: AgentChannel = 'text'
    target: AgentTarget = 'mock_agent'
    description: str | None = None
    metadata: AgentMetadata | None = None

    @model_validator(mode='after')
    def validate_channel_target_pair(self):
        validate_agent_channel_target(self.channel, self.target)
        return self


class AgentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str | None = Field(default=None, min_length=1, max_length=120)
    channel: AgentChannel | None = None
    target: AgentTarget | None = None
    description: str | None = None
    metadata: AgentMetadata | None = None

    @model_validator(mode='after')
    def validate_channel_target_pair(self):
        if self.channel is not None and self.target is not None:
            validate_agent_channel_target(self.channel, self.target)
        return self
