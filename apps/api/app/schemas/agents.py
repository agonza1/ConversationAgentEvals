from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.acc_connection import normalize_acc_base_url
from app.services.destination_validators import validate_e164_phone, validate_sip_uri
from app.services.target_secrets import HTTP_TARGET_SECRET_ID_PATTERN


AgentChannel = Literal['text', 'voice']
AgentEnvironment = Literal['local', 'staging', 'production']
AgentTarget = Literal[
    'mock_agent',
    'openai_codex',
    'offline_acc_fixture',
    'voice_fixture',  # legacy saved-replay target; new records are rejected
    'builtin_sample_voice',
    'pipecat_public_demo',
    'sip_agent',
    'phone_agent',
    'browser_webrtc_agent',
    'http_endpoint',
]

_TEXT_AGENT_TARGETS = frozenset({'mock_agent', 'openai_codex', 'offline_acc_fixture', 'http_endpoint'})
_VOICE_AGENT_TARGETS = frozenset(
    {
        'voice_fixture',
        'builtin_sample_voice',
        'pipecat_public_demo',
        'sip_agent',
        'phone_agent',
        'browser_webrtc_agent',
    }
)
_EXTERNAL_VOICE_TARGETS = frozenset({'sip_agent', 'phone_agent', 'browser_webrtc_agent'})


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

    `secret_ref` is an opaque credential ID, never an environment-variable name
    or secret value. The server maps it into a dedicated HTTP-target namespace.
    """

    model_config = ConfigDict(extra='forbid')

    endpoint_url: str | None = None
    auth_type: Literal['none', 'bearer_secret', 'api_key_secret'] = 'none'
    secret_ref: str | None = Field(default=None, pattern=HTTP_TARGET_SECRET_ID_PATTERN)
    api_key_header: str = Field(default='x-api-key', min_length=1, max_length=80)
    response_path: str = Field(default='response', min_length=1, max_length=160)
    timeout_ms: int = Field(default=15000, ge=500, le=120000)
    sip_uri: str | None = None
    phone_number: str | None = None
    acc_base_url: str | None = None

    @model_validator(mode='after')
    def validate_auth_reference(self):
        if self.auth_type != 'none' and not self.secret_ref:
            raise ValueError('Authenticated connections require a configured credential ID.')
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
        validate_new_target_availability(self.target)
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
        if self.target is not None:
            validate_new_target_availability(self.target)
        return self


def validate_agent_connection(target: AgentTarget, connection: AgentConnection) -> None:
    if target == 'pipecat_public_demo':
        endpoint = (connection.endpoint_url or '').strip()
        if not endpoint:
            raise ValueError('Pipecat demo targets require connection.endpoint_url.')
        parsed = urlparse(endpoint)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError('Pipecat demo targets must use https://www.pipecat.ai/.') from exc
        if (
            parsed.scheme != 'https'
            or parsed.hostname != 'www.pipecat.ai'
            or parsed.username
            or parsed.password
            or port is not None
            or parsed.path not in {'', '/'}
        ):
            raise ValueError('Pipecat demo targets must use https://www.pipecat.ai/.')
        if parsed.query or parsed.fragment:
            raise ValueError('Pipecat demo target URLs cannot contain query strings or fragments.')
        if connection.auth_type != 'none' or connection.secret_ref:
            raise ValueError('The public Pipecat demo target does not accept stored credentials.')
        if connection.sip_uri or connection.phone_number or connection.acc_base_url:
            raise ValueError('Pipecat demo targets cannot include ACC voice destination fields.')
        return

    if target == 'http_endpoint':
        endpoint = (connection.endpoint_url or '').strip()
        if not endpoint:
            raise ValueError('HTTP endpoint targets require connection.endpoint_url.')
        parsed = urlparse(endpoint)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise ValueError('connection.endpoint_url must be an absolute http:// or https:// URL.')
        if parsed.username or parsed.password:
            raise ValueError('Do not embed credentials in endpoint URLs; use connection.secret_ref.')
        if parsed.query or parsed.fragment:
            raise ValueError(
                'Endpoint URLs cannot contain query strings or fragments; use connection.secret_ref for credentials.'
            )
        if connection.sip_uri or connection.phone_number or connection.acc_base_url:
            raise ValueError('HTTP endpoint targets cannot include voice destination fields.')
        return

    if target == 'sip_agent':
        if connection.phone_number:
            raise ValueError('SIP targets cannot include a phone number; use Phone agent instead.')
        if not connection.sip_uri:
            raise ValueError('SIP targets require connection.sip_uri.')
        if not connection.acc_base_url:
            raise ValueError('SIP targets require connection.acc_base_url.')
        validate_sip_uri(connection.sip_uri)
        normalize_acc_base_url(connection.acc_base_url)
        return

    if target == 'phone_agent':
        if connection.sip_uri:
            raise ValueError('Phone targets cannot include a SIP URI; use SIP agent instead.')
        if not connection.phone_number:
            raise ValueError('Phone targets require connection.phone_number.')
        if not connection.acc_base_url:
            raise ValueError('Phone targets require connection.acc_base_url.')
        validate_e164_phone(connection.phone_number)
        normalize_acc_base_url(connection.acc_base_url)
        return

    if target == 'browser_webrtc_agent':
        if not connection.acc_base_url:
            raise ValueError('Browser WebRTC targets require connection.acc_base_url.')
        if connection.sip_uri or connection.phone_number:
            raise ValueError('Browser WebRTC targets cannot include SIP or phone destinations.')
        normalize_acc_base_url(connection.acc_base_url)
        return

    if connection.sip_uri or connection.phone_number or connection.acc_base_url:
        raise ValueError('Built-in and text targets cannot include ACC voice destination fields.')


def validate_new_target_availability(target: AgentTarget) -> None:
    if target in {'offline_acc_fixture', 'voice_fixture'}:
        raise ValueError('Saved conversation replay is evidence, not an agent target. Use Eval evidence instead.')
    if target in _EXTERNAL_VOICE_TARGETS:
        raise ValueError(
            'This ACC destination cannot be created yet because its CAE execution adapter is not implemented.'
        )
