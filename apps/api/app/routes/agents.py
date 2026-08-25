from __future__ import annotations

import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException

from app.schemas.agents import AgentCreateRequest, AgentTarget, AgentUpdateRequest
from app.services import agent_store
from app.services.run_provenance import execution_defaults_for_target
from app.services.signalwire_holyguacamole_target import SIGNALWIRE_PUBLIC_GATE_ENV
from app.services.target_secrets import resolve_http_target_secret


router = APIRouter(prefix='/api/agents', tags=['agents'])


@router.get('')
def list_agents():
    return {'agents': agent_store.list_agents()}


@router.post('')
def create_agent(payload: AgentCreateRequest):
    try:
        return agent_store.create_agent(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/options')
def list_agent_options():
    return {
        'targets': [_target_option(target) for target in _TARGET_OPTIONS],
        'testers': _tester_options(),
        'executors': _executor_options(),
    }


@router.get('/{agent_id}/readiness')
def get_agent_readiness(agent_id: str):
    agent = agent_store.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail='Agent not found.')
    return _readiness_for_agent(agent)


@router.get('/{agent_id}')
def get_agent(agent_id: str):
    agent = agent_store.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail='Agent not found.')
    return agent


@router.patch('/{agent_id}')
def update_agent(agent_id: str, payload: AgentUpdateRequest):
    try:
        agent = agent_store.update_agent(agent_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if agent is None:
        raise HTTPException(status_code=404, detail='Agent not found.')
    return agent


@router.delete('/{agent_id}')
def delete_agent(agent_id: str):
    try:
        deleted = agent_store.delete_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail='Agent not found.')
    return {'ok': True, 'id': agent_id}


_TARGET_OPTIONS: tuple[AgentTarget, ...] = (
    'http_endpoint',
    'openai_codex',
    'mock_agent',
    'builtin_sample_voice',
    'pipecat_public_demo',
    'signalwire_holy_guacamole',
    'browser_webrtc_agent',
    'sip_agent',
    'phone_agent',
)

_TARGET_LABELS: dict[AgentTarget, str] = {
    'http_endpoint': 'HTTP JSON chat endpoint',
    'openai_codex': 'Connected OpenAI prompt agent',
    'mock_agent': 'Built-in sample text agent',
    'builtin_sample_voice': 'Built-in generalist voice agent',
    'pipecat_public_demo': 'Pipecat demo',
    'signalwire_holy_guacamole': 'Holy Guacamole SignalWire',
    'browser_webrtc_agent': 'ACC browser WebRTC',
    'sip_agent': 'ACC SIP URI',
    'phone_agent': 'ACC phone number',
    'offline_acc_fixture': 'Saved ACC text replay',
    'voice_fixture': 'Saved voice replay',
}

_TARGET_CHANNELS: dict[AgentTarget, str] = {
    'http_endpoint': 'text',
    'openai_codex': 'text',
    'mock_agent': 'text',
    'offline_acc_fixture': 'text',
    'builtin_sample_voice': 'voice',
    'pipecat_public_demo': 'voice',
    'signalwire_holy_guacamole': 'voice',
    'voice_fixture': 'voice',
    'browser_webrtc_agent': 'voice',
    'sip_agent': 'voice',
    'phone_agent': 'voice',
}

_TARGET_GROUPS: dict[AgentTarget, str] = {
    'http_endpoint': 'live_connection',
    'openai_codex': 'live_connection',
    'mock_agent': 'built_in_sample',
    'builtin_sample_voice': 'built_in_sample',
    'pipecat_public_demo': 'live_connection',
    'signalwire_holy_guacamole': 'live_connection',
    'browser_webrtc_agent': 'planned_live_connection',
    'sip_agent': 'planned_live_connection',
    'phone_agent': 'planned_live_connection',
    'offline_acc_fixture': 'saved_replay',
    'voice_fixture': 'saved_replay',
}

_CONNECTION_REQUIREMENTS: dict[AgentTarget, tuple[str, ...]] = {
    'http_endpoint': ('endpoint_url',),
    'pipecat_public_demo': ('endpoint_url',),
    'signalwire_holy_guacamole': ('endpoint_url',),
    'browser_webrtc_agent': ('acc_base_url',),
    'sip_agent': ('acc_base_url', 'sip_uri'),
    'phone_agent': ('acc_base_url', 'phone_number'),
}

_COMPATIBLE_TARGETS_BY_TESTER: dict[str, tuple[AgentTarget, ...]] = {
    'scenario_simulator': ('http_endpoint', 'openai_codex', 'mock_agent'),
    'pipecat_tester': (
        'builtin_sample_voice',
        'pipecat_public_demo',
        'signalwire_holy_guacamole',
        'browser_webrtc_agent',
        'sip_agent',
        'phone_agent',
    ),
    'fixture_replay': ('offline_acc_fixture', 'voice_fixture'),
}

_COMPATIBLE_TARGETS_BY_EXECUTOR: dict[str, tuple[AgentTarget, ...]] = {
    'local_async_runner': ('http_endpoint', 'openai_codex', 'mock_agent'),
    'cae_local_audio_loop': ('builtin_sample_voice',),
    'pipecat_public_daily': ('pipecat_public_demo',),
    'signalwire_public_webrtc': ('signalwire_holy_guacamole',),
    'evidence_replay': ('offline_acc_fixture', 'voice_fixture'),
    'acc_browser_webrtc': ('browser_webrtc_agent',),
    'acc_sip': ('sip_agent',),
    'acc_phone': ('phone_agent',),
}

_TESTER_OPTIONS: tuple[dict[str, object], ...] = (
    {
        'id': 'scenario_simulator',
        'label': 'Scenario user',
        'channel': 'text',
        'description': 'Generates user turns from the selected scenario and adapts to target replies.',
        'available': True,
    },
    {
        'id': 'pipecat_tester',
        'label': 'Pipecat tester',
        'channel': 'voice',
        'description': 'Synthesizes caller speech, observes target audio, and records current-run media evidence.',
        'available': True,
    },
    {
        'id': 'fixture_replay',
        'label': 'Fixture replay',
        'channel': 'text_or_voice',
        'description': 'Replays saved evidence for regression checks; it is not a live target driver.',
        'available': True,
    },
)

_EXECUTOR_OPTIONS: tuple[dict[str, object], ...] = (
    {
        'id': 'local_async_runner',
        'label': 'Local async runner',
        'channel': 'text',
        'description': 'Invokes built-in, OpenAI, or HTTP text targets and stores black-box response evidence.',
        'available': True,
    },
    {
        'id': 'cae_local_audio_loop',
        'label': 'CAE local audio loop',
        'channel': 'voice',
        'description': 'Runs separate local Pipecat tester and target participants through rtc-asr and Kokoro.',
        'available': True,
    },
    {
        'id': 'pipecat_public_daily',
        'label': 'Pipecat public Daily',
        'channel': 'voice',
        'description': 'Joins the public Pipecat demo with a direct Daily transport and captures current-run evidence.',
        'available': True,
    },
    {
        'id': 'signalwire_public_webrtc',
        'label': 'SignalWire public WebRTC',
        'channel': 'voice',
        'description': 'Connects to the public Holy Guacamole SignalWire target behind the explicit run gate.',
        'available': True,
    },
    {
        'id': 'evidence_replay',
        'label': 'Evidence replay',
        'channel': 'text_or_voice',
        'description': 'Scores saved conversations without launching a target or tester.',
        'available': True,
    },
    {
        'id': 'acc_browser_webrtc',
        'label': 'ACC browser WebRTC',
        'channel': 'voice',
        'description': 'Planned ACC-owned browser media adapter; readiness is separate from executability.',
        'available': False,
    },
    {
        'id': 'acc_sip',
        'label': 'ACC SIP',
        'channel': 'voice',
        'description': 'Planned ACC-owned SIP adapter; readiness is separate from executability.',
        'available': False,
    },
    {
        'id': 'acc_phone',
        'label': 'ACC phone',
        'channel': 'voice',
        'description': 'Planned ACC-owned phone/PSTN adapter; readiness is separate from executability.',
        'available': False,
    },
)


def _target_option(target: AgentTarget) -> dict[str, object]:
    if target == 'pipecat_public_demo':
        return {
            'id': target,
            'label': _TARGET_LABELS[target],
            'channel': _TARGET_CHANNELS[target],
            'group': _TARGET_GROUPS[target],
            'available': True,
            'unavailable_reason': None,
            'requires_connection': ['endpoint_url'],
            'default_connection': {'endpoint_url': 'https://www.pipecat.ai/'},
            'defaults': {
                'mode': 'pipecat_webrtc',
                'tester_id': 'pipecat_tester',
                'executor_id': 'pipecat_public_daily',
                'audio_transport': 'pipecat_daily_webrtc',
            },
        }
    if target == 'signalwire_holy_guacamole':
        return {
            'id': target,
            'label': _TARGET_LABELS[target],
            'channel': _TARGET_CHANNELS[target],
            'group': _TARGET_GROUPS[target],
            'available': True,
            'unavailable_reason': None,
            'requires_connection': ['endpoint_url'],
            'default_connection': {'endpoint_url': 'https://holyguacamole.signalwire.me/'},
            'defaults': {
                'mode': 'pipecat_webrtc',
                'tester_id': 'pipecat_tester',
                'executor_id': 'signalwire_public_webrtc',
                'audio_transport': 'signalwire_webrtc',
                'max_exchanges': 1,
                'max_exchanges_configurable': True,
                'max_exchanges_limit': 2,
            },
        }
    defaults = execution_defaults_for_target(target)
    unavailable = target in {'browser_webrtc_agent', 'sip_agent', 'phone_agent'}
    return {
        'id': target,
        'label': _TARGET_LABELS[target],
        'channel': _TARGET_CHANNELS[target],
        'group': _TARGET_GROUPS[target],
        'available': not unavailable,
        'unavailable_reason': (
            'CAE can test ACC readiness, but cannot execute this adapter end to end yet.'
            if unavailable
            else None
        ),
        'requires_connection': list(_CONNECTION_REQUIREMENTS.get(target, ())),
        'defaults': defaults.model_dump(mode='json'),
    }


def _tester_options() -> list[dict[str, object]]:
    return [
        {
            **option,
            'compatible_target_ids': list(_COMPATIBLE_TARGETS_BY_TESTER[str(option['id'])]),
        }
        for option in _TESTER_OPTIONS
    ]


def _executor_options() -> list[dict[str, object]]:
    return [
        {
            **option,
            'compatible_target_ids': list(_COMPATIBLE_TARGETS_BY_EXECUTOR[str(option['id'])]),
        }
        for option in _EXECUTOR_OPTIONS
    ]


def _readiness_for_agent(agent: dict) -> dict[str, object]:
    target = str(agent.get('target') or '')
    connection = agent.get('connection') if isinstance(agent.get('connection'), dict) else {}
    option = _target_option(target) if target in _TARGET_OPTIONS else None
    checks: list[dict[str, object]] = []

    def add_check(name: str, ok: bool, message: str) -> None:
        checks.append({'name': name, 'ok': ok, 'message': message})

    if option is not None and option.get('available') is False:
        add_check('adapter_available', False, str(option.get('unavailable_reason') or 'Adapter is unavailable.'))
    else:
        add_check('adapter_available', True, 'Adapter can be selected for runs.')

    required_fields = list(_CONNECTION_REQUIREMENTS.get(target, ()))
    missing = [field for field in required_fields if not str(connection.get(field) or '').strip()]
    add_check(
        'connection_configuration',
        not missing,
        (
            'Required connection fields are present.'
            if not missing
            else f'Missing required connection fields: {", ".join(missing)}.'
        ),
    )

    auth_type = str(connection.get('auth_type') or 'none')
    if target == 'http_endpoint' and auth_type != 'none':
        secret_ref = str(connection.get('secret_ref') or '').strip()
        if not secret_ref:
            add_check('credential_reference', False, 'Authenticated HTTP targets require a credential ID.')
        else:
            try:
                resolve_http_target_secret(secret_ref)
            except ValueError as exc:
                add_check('credential_reference', False, str(exc))
            else:
                add_check('credential_reference', True, 'Credential ID resolves in the HTTP target namespace.')
    elif target == 'http_endpoint':
        add_check('credential_reference', True, 'No credential is required for this HTTP target.')

    if target == 'signalwire_holy_guacamole':
        gate_enabled = str(os.getenv(SIGNALWIRE_PUBLIC_GATE_ENV) or '').strip().lower() in {'1', 'true', 'yes'}
        add_check(
            'signalwire_public_gate',
            gate_enabled,
            (
                f'{SIGNALWIRE_PUBLIC_GATE_ENV} is enabled for public SignalWire execution.'
                if gate_enabled
                else f'{SIGNALWIRE_PUBLIC_GATE_ENV}=1 is required before queueing public SignalWire execution.'
            ),
        )
        add_check(*_signalwire_caller_tts_readiness())

    executable = all(item['ok'] for item in checks)
    return {
        'agent_id': agent.get('id'),
        'target': target,
        'environment': agent.get('environment') or 'local',
        'executable': executable,
        'checks': checks,
        'defaults': (
            option.get('defaults')
            if option is not None
            else execution_defaults_for_target(target).model_dump(mode='json')
        ),
    }


def _signalwire_caller_tts_readiness() -> tuple[str, bool, str]:
    service_url = str(os.getenv('KOKORO_BASE_URL') or '').strip().rstrip('/')
    if not service_url:
        return (
            'signalwire_caller_tts',
            False,
            'KOKORO_BASE_URL is required for public SignalWire caller audio synthesis before queueing.',
        )
    request = Request(
        f'{service_url}/health',
        headers={'accept': 'application/json'},
        method='GET',
    )
    try:
        with urlopen(request, timeout=2.0) as response:  # noqa: S310
            response.read()
    except HTTPError as exc:
        return (
            'signalwire_caller_tts',
            False,
            f'KOKORO_BASE_URL caller TTS health returned HTTP {exc.code}.',
        )
    except URLError:
        return (
            'signalwire_caller_tts',
            False,
            'KOKORO_BASE_URL caller TTS health is unreachable.',
        )
    except (TimeoutError, OSError):
        return (
            'signalwire_caller_tts',
            False,
            'KOKORO_BASE_URL caller TTS health timed out.',
        )
    return (
        'signalwire_caller_tts',
        True,
        'KOKORO_BASE_URL caller TTS health is reachable for public SignalWire execution.',
    )
