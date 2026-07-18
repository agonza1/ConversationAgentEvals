from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.destination_validators import validate_e164_phone, validate_sip_uri
from app.services.run_provenance import (
    acc_connection_status,
    external_voice_target_blocked,
    is_acc_destination_ready,
    normalize_agent_target,
)


AgentChannel = Literal['text', 'voice']
AgentTarget = Literal[
    'mock_agent',
    'openai_codex',
    'offline_acc_fixture',
    'voice_fixture',  # legacy alias → builtin_sample_voice
    'builtin_sample_voice',
    'sip_agent',
    'phone_agent',
    'browser_webrtc_agent',
]

_TEXT_AGENT_TARGETS = frozenset({'mock_agent', 'openai_codex', 'offline_acc_fixture'})
_VOICE_AGENT_TARGETS = frozenset(
    {
        'voice_fixture',
        'builtin_sample_voice',
        'sip_agent',
        'phone_agent',
        'browser_webrtc_agent',
    }
)
_CREATABLE_VOICE_TARGETS = frozenset({'voice_fixture', 'builtin_sample_voice'})


def validate_agent_channel_target(channel: AgentChannel, target: AgentTarget) -> None:
    allowed_targets = _VOICE_AGENT_TARGETS if channel == 'voice' else _TEXT_AGENT_TARGETS
    if target not in allowed_targets:
        allowed = ', '.join(f'"{item}"' for item in sorted(allowed_targets))
        raise ValueError(f'Channel "{channel}" requires one of: {allowed}.')


def _validate_destination_fields(
    *,
    target: AgentTarget,
    sip_uri: str | None,
    phone_number: str | None,
    acc_base_url: str | None,
    enforce_acc_gate: bool,
) -> tuple[str | None, str | None, str | None]:
    normalized = normalize_agent_target(target)
    sip_value = (sip_uri or '').strip() or None
    phone_value = (phone_number or '').strip() or None
    acc_value = (acc_base_url or '').strip() or None
    if enforce_acc_gate and external_voice_target_blocked(normalized):
        if not acc_value:
            raise ValueError('ACC base URL is required for external voice destinations.')
        if not is_acc_destination_ready(acc_value, normalized):
            status = acc_connection_status(base_url=acc_value)
            destination = (status.get('destinations') or {}).get(normalized) or {}
            reason = destination.get('label') or status.get('message') or 'Requires ACC connection.'
            raise ValueError(f'{reason} Test the ACC connection before creating this target.')

    if normalized == 'sip_agent':
        if phone_value:
            raise ValueError('SIP agent destination cannot include a phone number; use Phone agent instead.')
        if not sip_value:
            raise ValueError('SIP agent requires a SIP URI.')
        sip_value = validate_sip_uri(sip_value)
        return sip_value, None, acc_value

    if normalized == 'phone_agent':
        if sip_value:
            raise ValueError('Phone agent destination cannot include a SIP URI; use SIP agent instead.')
        if not phone_value:
            raise ValueError('Phone agent requires an E.164 phone number.')
        phone_value = validate_e164_phone(phone_value)
        return None, phone_value, acc_value

    if normalized == 'browser_webrtc_agent':
        return None, None, acc_value

    # Built-in / text destinations do not store dial fields.
    return None, None, None


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
    sip_uri: str | None = None
    phone_number: str | None = None
    acc_base_url: str | None = None
    metadata: AgentMetadata = Field(default_factory=AgentMetadata)
    created_at: str
    updated_at: str

    @model_validator(mode='after')
    def validate_channel_target_pair(self):
        validate_agent_channel_target(self.channel, self.target)
        sip_uri, phone_number, acc_base_url = _validate_destination_fields(
            target=self.target,
            sip_uri=self.sip_uri,
            phone_number=self.phone_number,
            acc_base_url=self.acc_base_url,
            enforce_acc_gate=False,
        )
        object.__setattr__(self, 'sip_uri', sip_uri)
        object.__setattr__(self, 'phone_number', phone_number)
        object.__setattr__(self, 'acc_base_url', acc_base_url)
        # Normalize legacy voice_fixture → builtin_sample_voice on read/write.
        if self.target == 'voice_fixture':
            object.__setattr__(self, 'target', 'builtin_sample_voice')
        return self


class AgentCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str | None = Field(default=None, min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    channel: AgentChannel = 'text'
    target: AgentTarget = 'mock_agent'
    description: str | None = None
    sip_uri: str | None = None
    phone_number: str | None = None
    acc_base_url: str | None = None
    metadata: AgentMetadata | None = None

    @model_validator(mode='after')
    def validate_channel_target_pair(self):
        validate_agent_channel_target(self.channel, self.target)
        sip_uri, phone_number, acc_base_url = _validate_destination_fields(
            target=self.target,
            sip_uri=self.sip_uri,
            phone_number=self.phone_number,
            acc_base_url=self.acc_base_url,
            enforce_acc_gate=True,
        )
        self.sip_uri = sip_uri
        self.phone_number = phone_number
        self.acc_base_url = acc_base_url
        if self.target == 'voice_fixture':
            self.target = 'builtin_sample_voice'
        return self


class AgentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str | None = Field(default=None, min_length=1, max_length=120)
    channel: AgentChannel | None = None
    target: AgentTarget | None = None
    description: str | None = None
    sip_uri: str | None = None
    phone_number: str | None = None
    acc_base_url: str | None = None
    metadata: AgentMetadata | None = None

    @model_validator(mode='after')
    def validate_channel_target_pair(self):
        if self.channel is not None and self.target is not None:
            validate_agent_channel_target(self.channel, self.target)
        if self.target is not None:
            sip_uri, phone_number, acc_base_url = _validate_destination_fields(
                target=self.target,
                sip_uri=self.sip_uri,
                phone_number=self.phone_number,
                acc_base_url=self.acc_base_url,
                enforce_acc_gate=True,
            )
            self.sip_uri = sip_uri
            self.phone_number = phone_number
            self.acc_base_url = acc_base_url
            if self.target == 'voice_fixture':
                self.target = 'builtin_sample_voice'
        return self
