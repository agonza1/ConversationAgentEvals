"""Target → Tester → Executor → Evidence provenance for execution runs.

Executor kinds (cae_local_audio_loop, acc_sip, …) are transports — not agent destinations.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict

TargetKind = Literal[
    'builtin_sample_text',
    'openai_text',
    'builtin_text_fixture',
    'builtin_sample_voice',
    'sip_agent',
    'phone_agent',
    'browser_webrtc_agent',
    'unknown',
]
TesterKind = Literal[
    'scenario_policy',
    'pipecat_tester',
    'fixture_scheduler',
    'openai_text_agent',
    'mock_text_agent',
    'unknown',
]
ExecutorKind = Literal[
    'cae_local_audio_loop',
    'acc_browser_webrtc',
    'acc_sip',
    'acc_phone',
    'none',
]
MediaSource = Literal['local_loop', 'acc_live', 'saved_replay', 'none']

BUILTIN_SAMPLE_VOICE_HONESTY = (
    'Built-in sample agent · local audio loop · no phone or SIP call'
)

_VOICE_SAMPLE_TARGETS = frozenset({'builtin_sample_voice', 'voice_fixture'})
_EXTERNAL_VOICE_TARGETS = frozenset({'sip_agent', 'phone_agent', 'browser_webrtc_agent'})

_COMPATIBLE_EXECUTORS: dict[str, frozenset[str]] = {
    'mock_agent': frozenset({'none'}),
    'openai_codex': frozenset({'none'}),
    'offline_acc_fixture': frozenset({'none'}),
    'builtin_sample_voice': frozenset({'cae_local_audio_loop'}),
    'voice_fixture': frozenset({'cae_local_audio_loop'}),
    'sip_agent': frozenset({'acc_sip'}),
    'phone_agent': frozenset({'acc_phone'}),
    'browser_webrtc_agent': frozenset({'acc_browser_webrtc'}),
}

_ACC_EXECUTORS = frozenset({'acc_sip', 'acc_phone', 'acc_browser_webrtc'})
_ACC_READINESS_PATH = '/api/pipecat-media-engine/readiness'
_ACC_CONNECTION_TTL_SECONDS = 15 * 60
_ACC_CONNECTIONS: dict[str, tuple[float, dict[str, Any]]] = {}
_ACC_CONNECTION_LOCK = threading.Lock()


class ExecutionRunProvenance(BaseModel):
    model_config = ConfigDict(extra='forbid')

    target_kind: TargetKind
    tester_kind: TesterKind
    executor_kind: ExecutorKind
    media_source: MediaSource
    live_external_connection: bool = False
    saved_evidence: bool = False
    synthetic_audio: bool = False
    honesty_label: str | None = None


def normalize_agent_target(target: str | None) -> str:
    value = str(target or '').strip()
    if value == 'voice_fixture':
        return 'builtin_sample_voice'
    return value or 'mock_agent'


def target_kind_for_agent_target(target: str | None, *, channel: str | None = None) -> TargetKind:
    normalized = normalize_agent_target(target)
    mapping: dict[str, TargetKind] = {
        'mock_agent': 'builtin_sample_text',
        'openai_codex': 'openai_text',
        'offline_acc_fixture': 'builtin_text_fixture',
        'builtin_sample_voice': 'builtin_sample_voice',
        'sip_agent': 'sip_agent',
        'phone_agent': 'phone_agent',
        'browser_webrtc_agent': 'browser_webrtc_agent',
    }
    if normalized in mapping:
        return mapping[normalized]
    if channel == 'voice':
        return 'builtin_sample_voice'
    return 'unknown'


def default_executor_for_target(target: str | None) -> ExecutorKind:
    normalized = normalize_agent_target(target)
    if normalized in _VOICE_SAMPLE_TARGETS:
        return 'cae_local_audio_loop'
    if normalized == 'sip_agent':
        return 'acc_sip'
    if normalized == 'phone_agent':
        return 'acc_phone'
    if normalized == 'browser_webrtc_agent':
        return 'acc_browser_webrtc'
    return 'none'


def assert_executor_compatible(*, agent_target: str | None, executor_kind: ExecutorKind) -> None:
    normalized = normalize_agent_target(agent_target)
    allowed = _COMPATIBLE_EXECUTORS.get(normalized, frozenset({'none'}))
    # Legacy voice_fixture agents map to builtin_sample_voice.
    if normalized == 'builtin_sample_voice':
        allowed = _COMPATIBLE_EXECUTORS['builtin_sample_voice']
    if executor_kind not in allowed:
        raise ValueError(
            f'Executor "{executor_kind}" is not compatible with target "{normalized}". '
            f'Allowed: {", ".join(sorted(allowed))}.'
        )
    if executor_kind in _ACC_EXECUTORS:
        raise ValueError(
            f'Executor "{executor_kind}" requires an ACC connection. '
            'Live SIP, phone/PSTN, and browser WebRTC stay owned by Agentic Contact Center.'
        )


def resolve_execution_mode_for_executor(executor_kind: ExecutorKind) -> str:
    """Map executor transport to today's ExecutionMode wire values."""
    if executor_kind == 'cae_local_audio_loop':
        return 'pipecat_webrtc'
    return 'text_callable'


def build_run_provenance(
    *,
    agent: dict[str, Any] | None,
    agent_target: str | None,
    channel: str | None,
    executor_kind: ExecutorKind,
    mode: str,
    text_callable: str | None = None,
    evaluate: bool = True,
) -> ExecutionRunProvenance:
    target = normalize_agent_target(agent_target or (agent or {}).get('target'))
    channel_value = channel or str((agent or {}).get('channel') or 'text')
    target_kind = target_kind_for_agent_target(target, channel=channel_value)

    if executor_kind == 'cae_local_audio_loop' or mode == 'pipecat_webrtc':
        tester_kind: TesterKind = 'pipecat_tester'
        media_source: MediaSource = 'local_loop'
        synthetic_audio = True
        saved_evidence = False
        honesty = BUILTIN_SAMPLE_VOICE_HONESTY
    elif mode == 'voice_fixture':
        # Legacy / evidence-evaluation path: saved ACC conversation replay.
        tester_kind = 'fixture_scheduler'
        media_source = 'saved_replay'
        synthetic_audio = True
        saved_evidence = True
        honesty = 'Saved conversation replay · evidence evaluation · no phone or SIP call'
    elif text_callable == 'offline_acc_fixture' or target == 'offline_acc_fixture':
        tester_kind = 'fixture_scheduler'
        media_source = 'none'
        synthetic_audio = False
        saved_evidence = True
        honesty = 'Built-in text fixture · saved evidence'
    elif text_callable == 'openai_codex' or target == 'openai_codex':
        tester_kind = 'openai_text_agent'
        media_source = 'none'
        synthetic_audio = False
        saved_evidence = False
        honesty = None
    else:
        tester_kind = 'mock_text_agent' if target == 'mock_agent' else 'scenario_policy'
        media_source = 'none'
        synthetic_audio = False
        saved_evidence = False
        honesty = None

    live = executor_kind in _ACC_EXECUTORS
    return ExecutionRunProvenance(
        target_kind=target_kind,
        tester_kind=tester_kind,
        executor_kind=executor_kind,
        media_source=media_source,
        live_external_connection=live,
        saved_evidence=saved_evidence,
        synthetic_audio=synthetic_audio,
        honesty_label=honesty,
    )


def external_voice_target_blocked(target: str) -> bool:
    return normalize_agent_target(str(target)) in _EXTERNAL_VOICE_TARGETS


def normalize_acc_base_url(value: str) -> str:
    raw = (value or '').strip().rstrip('/')
    parsed = urlparse(raw)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('ACC URL must be an http:// or https:// URL with a host.')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('ACC URL cannot include credentials, a query, or a fragment.')
    if parsed.path not in {'', '/'}:
        raise ValueError('Enter the ACC base URL only (for example http://127.0.0.1:8026).')
    return raw


def _destination_capabilities(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    adapters = (
        ((payload.get('sharedEngineContract') or {}).get('requiredAdapters') or [])
        if isinstance(payload.get('sharedEngineContract'), dict)
        else []
    )
    by_id = {
        str(item.get('id')): item
        for item in adapters
        if isinstance(item, dict) and item.get('id')
    }
    browser = by_id.get('browser_webrtc') or {}
    sip = by_id.get('sip_freeswitch_verto') or {}
    phone = by_id.get('signalwire_sip_trunk') or {}
    return {
        'browser_webrtc_agent': {
            'creatable': browser.get('implementedNow') is True,
            'executor_kind': 'acc_browser_webrtc',
            'label': (
                'Ready through ACC'
                if browser.get('implementedNow') is True
                else str(browser.get('blocker') or 'ACC browser WebRTC is not ready.')
            ),
        },
        'sip_agent': {
            'creatable': (
                sip.get('implementedNow') is True
                and sip.get('liveMediaProofComplete') is True
            ),
            'executor_kind': 'acc_sip',
            'label': (
                'Ready through ACC'
                if sip.get('implementedNow') is True
                and sip.get('liveMediaProofComplete') is True
                else str(sip.get('blocker') or 'ACC SIP/Verto is not ready.')
            ),
        },
        'phone_agent': {
            'creatable': (
                phone.get('implementedNow') is True
                and phone.get('liveMediaProofComplete') is True
            ),
            'executor_kind': 'acc_phone',
            'label': (
                'Ready through ACC'
                if phone.get('implementedNow') is True
                and phone.get('liveMediaProofComplete') is True
                else str(phone.get('blocker') or 'ACC phone/PSTN trunk is not ready.')
            ),
        },
    }


def test_acc_connection(base_url: str) -> dict[str, Any]:
    """Probe the official ACC media readiness route and cache a successful result."""
    normalized = normalize_acc_base_url(base_url)
    readiness_url = f'{normalized}{_ACC_READINESS_PATH}'
    try:
        with httpx.Client(timeout=4.0, follow_redirects=False) as client:
            response = client.get(readiness_url, headers={'Accept': 'application/json'})
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return acc_connection_status(
            base_url=normalized,
            message=f'Could not connect to ACC readiness: {exc}',
        )
    if not isinstance(payload, dict) or payload.get('ok') is not True or payload.get('route') != _ACC_READINESS_PATH:
        return acc_connection_status(
            base_url=normalized,
            message='The server responded, but it is not the expected Agentic Contact Center readiness API.',
        )

    destinations = _destination_capabilities(payload)
    result = {
        'connected': True,
        'status': 'connected',
        'label': 'ACC connected',
        'message': 'Connection verified against the official ACC media readiness API.',
        'base_url': normalized,
        'readiness_url': readiness_url,
        'destinations': destinations,
    }
    with _ACC_CONNECTION_LOCK:
        _ACC_CONNECTIONS[normalized] = (time.monotonic(), result)
    return result


def is_acc_destination_ready(base_url: str | None, target: str) -> bool:
    if not base_url:
        return False
    try:
        normalized = normalize_acc_base_url(base_url)
    except ValueError:
        return False
    with _ACC_CONNECTION_LOCK:
        cached = _ACC_CONNECTIONS.get(normalized)
    if not cached or time.monotonic() - cached[0] > _ACC_CONNECTION_TTL_SECONDS:
        return False
    destination = (cached[1].get('destinations') or {}).get(normalize_agent_target(target)) or {}
    return destination.get('creatable') is True


def reset_acc_connections_for_tests() -> None:
    with _ACC_CONNECTION_LOCK:
        _ACC_CONNECTIONS.clear()


def acc_connection_status(
    *,
    base_url: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    if base_url:
        try:
            normalized = normalize_acc_base_url(base_url)
        except ValueError:
            normalized = base_url
        with _ACC_CONNECTION_LOCK:
            cached = _ACC_CONNECTIONS.get(normalized)
        if cached and time.monotonic() - cached[0] <= _ACC_CONNECTION_TTL_SECONDS:
            return dict(cached[1])
    return {
        'connected': False,
        'status': 'requires_acc_connection',
        'label': 'Requires ACC connection',
        'message': message or (
            'Live SIP, phone/PSTN, FreeSWITCH, Verto, and browser WebRTC stay in '
            'Agentic Contact Center. Enter its URL and test the connection first.'
        ),
        'base_url': base_url,
        'readiness_url': None,
        'destinations': {
            'sip_agent': {
                'creatable': False,
                'executor_kind': 'acc_sip',
                'label': 'Test ACC connection to enable',
            },
            'phone_agent': {
                'creatable': False,
                'executor_kind': 'acc_phone',
                'label': 'Test ACC connection to check PSTN readiness',
            },
            'browser_webrtc_agent': {
                'creatable': False,
                'executor_kind': 'acc_browser_webrtc',
                'label': 'Test ACC connection to enable',
            },
        },
    }
