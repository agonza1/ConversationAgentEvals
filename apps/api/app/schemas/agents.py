from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


AgentChannel = Literal['text', 'voice']
AgentEnvironment = Literal['local', 'staging', 'production']
AgentTarget = Literal[
    'mock_agent',
    'openai_codex',
    'offline_acc_fixture',
    'voice_fixture',
    'http_endpoint',
]

_TEXT_AGENT_TARGETS = frozenset({'mock_agent', 'openai_codex', 'offline_acc_fixture', 'http_endpoint'})
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


class AgentConnection(BaseModel):
    """Executable connection details for an external target.

    `secret_ref` is an environment-variable name, never the secret value. This
    keeps registry artifacts safe to inspect and commit as test evidence.
    """

    model_config = ConfigDict(extra='forbid')

    endpoint_url: str | None = None
    auth_type: Literal['none', 'bearer_secret', 'api_key_secret'] = 'none'
    secret_ref: str | None = Field(default=None, pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    api_key_header: str = Field(default='x-api-key', min_length=1, max_length=80)
    response_path: str = Field(default='response', min_length=1, max_length=160)
    timeout_ms: int = Field(default=15000, ge=500, le=120000)

    @model_validator(mode='after')
    def validate_auth_reference(self):
        if self.auth_type != 'none' and not self.secret_ref:
            raise ValueError('Authenticated connections require a secret_ref environment variable name.')
        return self


class AgentRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str
    name: str
    channel: AgentChannel
    target: AgentTarget
    environment: AgentEnvironment = 'local'
    connection: AgentConnection = Field(default_factory=AgentConnection)
    description: str | None = None
    metadata: AgentMetadata = Field(default_factory=AgentMetadata)
    created_at: str
    updated_at: str

    @model_validator(mode='after')
    def validate_channel_target_pair(self):
        validate_agent_channel_target(self.channel, self.target)
        validate_agent_connection(self.target, self.connection)
        return self


class AgentCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str | None = Field(default=None, min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    channel: AgentChannel = 'text'
    target: AgentTarget = 'mock_agent'
    environment: AgentEnvironment = 'local'
    connection: AgentConnection = Field(default_factory=AgentConnection)
    description: str | None = None
    metadata: AgentMetadata | None = None

    @model_validator(mode='after')
    def validate_channel_target_pair(self):
        validate_agent_channel_target(self.channel, self.target)
        validate_agent_connection(self.target, self.connection)
        return self


class AgentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str | None = Field(default=None, min_length=1, max_length=120)
    channel: AgentChannel | None = None
    target: AgentTarget | None = None
    environment: AgentEnvironment | None = None
    connection: AgentConnection | None = None
    description: str | None = None
    metadata: AgentMetadata | None = None

    @model_validator(mode='after')
    def validate_channel_target_pair(self):
        if self.channel is not None and self.target is not None:
            validate_agent_channel_target(self.channel, self.target)
        return self


def validate_agent_connection(target: AgentTarget, connection: AgentConnection) -> None:
    if target != 'http_endpoint':
        return
    endpoint = (connection.endpoint_url or '').strip()
    if not endpoint:
        raise ValueError('HTTP endpoint targets require connection.endpoint_url.')
    parsed = urlparse(endpoint)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('connection.endpoint_url must be an absolute http:// or https:// URL.')
    if parsed.username or parsed.password:
        raise ValueError('Do not embed credentials in endpoint URLs; use connection.secret_ref.')
    if parsed.query or parsed.fragment:
        raise ValueError('Endpoint URLs cannot contain query strings or fragments; use connection.secret_ref for credentials.')
