from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentChannel = Literal['text', 'voice']
AgentTarget = Literal['mock_agent', 'openai_codex', 'offline_acc_fixture', 'voice_fixture']


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


class AgentCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str | None = Field(default=None, min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    channel: AgentChannel = 'text'
    target: AgentTarget = 'mock_agent'
    description: str | None = None
    metadata: AgentMetadata | None = None


class AgentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str | None = Field(default=None, min_length=1, max_length=120)
    channel: AgentChannel | None = None
    target: AgentTarget | None = None
    description: str | None = None
    metadata: AgentMetadata | None = None
