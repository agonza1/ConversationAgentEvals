"""Read-only readiness probing for ACC-owned live media adapters."""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx


_READINESS_PATH = '/api/pipecat-media-engine/readiness'
_CACHE_TTL_SECONDS = 15 * 60
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LOCK = threading.Lock()


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


def _capabilities(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    contract = payload.get('sharedEngineContract')
    adapters = contract.get('requiredAdapters') if isinstance(contract, dict) else []
    by_id = {
        str(item.get('id')): item
        for item in (adapters or [])
        if isinstance(item, dict) and item.get('id')
    }
    mappings = {
        'browser_webrtc_agent': ('browser_webrtc', 'acc_browser_webrtc'),
        'sip_agent': ('sip_freeswitch_verto', 'acc_sip'),
        'phone_agent': ('signalwire_sip_trunk', 'acc_phone'),
    }
    result: dict[str, dict[str, Any]] = {}
    for target, (adapter_id, executor_id) in mappings.items():
        adapter = by_id.get(adapter_id) or {}
        media_ready = adapter.get('implementedNow') is True
        if target != 'browser_webrtc_agent':
            media_ready = media_ready and adapter.get('liveMediaProofComplete') is True
        result[target] = {
            'acc_ready': media_ready,
            'cae_executor_available': False,
            'creatable': False,
            'executor_id': executor_id,
            'label': (
                'ACC media ready; CAE execution adapter is not implemented yet'
                if media_ready
                else str(adapter.get('blocker') or 'ACC media adapter is not ready.')
            ),
        }
    return result


def test_acc_connection(base_url: str) -> dict[str, Any]:
    normalized = normalize_acc_base_url(base_url)
    readiness_url = f'{normalized}{_READINESS_PATH}'
    try:
        with httpx.Client(timeout=4.0, follow_redirects=False) as client:
            response = client.get(readiness_url, headers={'Accept': 'application/json'})
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return acc_connection_status(base_url=normalized, message=f'Could not connect to ACC readiness: {exc}')
    if not isinstance(payload, dict) or payload.get('ok') is not True or payload.get('route') != _READINESS_PATH:
        return acc_connection_status(
            base_url=normalized,
            message='The server responded, but it is not the expected Agentic Contact Center readiness API.',
        )
    result = {
        'connected': True,
        'status': 'connected_not_executable',
        'label': 'ACC connected',
        'message': 'ACC readiness verified. CAE live execution adapters remain unavailable in this release.',
        'base_url': normalized,
        'readiness_url': readiness_url,
        'destinations': _capabilities(payload),
    }
    with _LOCK:
        _CACHE[normalized] = (time.monotonic(), result)
    return result


def acc_connection_status(*, base_url: str | None = None, message: str | None = None) -> dict[str, Any]:
    if base_url:
        try:
            normalized = normalize_acc_base_url(base_url)
        except ValueError:
            normalized = base_url
        with _LOCK:
            cached = _CACHE.get(normalized)
        if cached and time.monotonic() - cached[0] <= _CACHE_TTL_SECONDS:
            return dict(cached[1])
    return {
        'connected': False,
        'status': 'requires_acc_connection',
        'label': 'Requires ACC connection',
        'message': message or (
            'ACC owns live SIP, phone/PSTN, FreeSWITCH, Verto, and browser WebRTC. '
            'Connection testing is available, but CAE execution adapters are not implemented yet.'
        ),
        'base_url': base_url,
        'readiness_url': None,
        'destinations': {
            target: {
                'acc_ready': False,
                'cae_executor_available': False,
                'creatable': False,
                'executor_id': executor,
                'label': 'Test ACC readiness; CAE execution adapter is still coming soon',
            }
            for target, executor in {
                'sip_agent': 'acc_sip',
                'phone_agent': 'acc_phone',
                'browser_webrtc_agent': 'acc_browser_webrtc',
            }.items()
        },
    }


def reset_acc_connections_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()
