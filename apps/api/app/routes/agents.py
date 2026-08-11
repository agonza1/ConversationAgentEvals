from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.agents import AgentCreateRequest, AgentTarget, AgentUpdateRequest
from app.services import agent_store
from app.services.run_provenance import execution_defaults_for_target


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
    return {'targets': [_target_option(target) for target in _TARGET_OPTIONS]}


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
                'executor_id': 'signalwire_public_browser',
                'audio_transport': 'signalwire_browser_webrtc',
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
