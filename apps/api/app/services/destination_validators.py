"""Validators for Target destinations (SIP URI vs E.164 phone).

These describe where the agent under test lives — not how the call is executed.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

_E164_RE = re.compile(r'^\+[1-9]\d{7,14}$')
_SIP_USER_RE = re.compile(r'^[\w\-.\+~%]+$')
_SIP_HOST_RE = re.compile(
    r'^(?:'
    r'(?:(?:[a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])\.)*'
    r'(?:[A-Za-z0-9]|[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9])'
    r'|'
    r'(?:\d{1,3}\.){3}\d{1,3}'
    r'|'
    r'\[[0-9A-Fa-f:]+\]'
    r')$'
)


def validate_e164_phone(value: str) -> str:
    """Accept E.164 only: +country + subscriber. Reject SIP URIs and bare nationals."""
    raw = (value or '').strip()
    if not raw:
        raise ValueError('Phone number is required.')
    if raw.lower().startswith(('sip:', 'sips:')):
        raise ValueError('Phone agent destination must be E.164, not a SIP URI.')
    if ' ' in raw or '-' in raw or '(' in raw:
        raise ValueError('Phone number must be E.164 without spaces or punctuation (e.g. +12125550123).')
    if not _E164_RE.fullmatch(raw):
        raise ValueError(
            'Phone number must be E.164 with a country code (e.g. +12125550123).'
        )
    return raw


def validate_sip_uri(value: str) -> str:
    """Validate a SIP/SIPS URI with urllib.parse (ports, params, IP hosts allowed)."""
    raw = (value or '').strip()
    if not raw:
        raise ValueError('SIP URI is required.')
    if raw.startswith('+') and '@' not in raw and _E164_RE.fullmatch(raw):
        raise ValueError('SIP agent destination must be a sip:/sips: URI, not an E.164 phone number.')

    parsed = urlparse(raw)
    scheme = (parsed.scheme or '').lower()
    if scheme not in {'sip', 'sips'}:
        raise ValueError('SIP URI must use the sip: or sips: scheme.')

    # urlparse('sip:user@host:5060;transport=tcp') → path holds user@host:port for some forms.
    # Prefer netloc when present; otherwise parse path as user@host[:port].
    authority = parsed.netloc or parsed.path
    if not authority:
        raise ValueError('SIP URI must include user@host.')

    # Strip URI parameters / headers that may appear after ;
    authority = authority.split(';', 1)[0].split('?', 1)[0]
    if '@' not in authority:
        raise ValueError('SIP URI must include user@host.')

    user, hostport = authority.rsplit('@', 1)
    user = unquote(user)
    if not user or not _SIP_USER_RE.fullmatch(user):
        raise ValueError('SIP URI user part is missing or invalid.')

    host = hostport
    if hostport.startswith('['):
        # IPv6 literal: [addr]:port
        end = hostport.find(']')
        if end < 0:
            raise ValueError('SIP URI IPv6 host is invalid.')
        host = hostport[: end + 1]
        rest = hostport[end + 1 :]
        if rest:
            if not rest.startswith(':'):
                raise ValueError('SIP URI host/port is invalid.')
            port = rest[1:]
            if not port.isdigit() or not (1 <= int(port) <= 65535):
                raise ValueError('SIP URI port must be 1–65535.')
    elif ':' in hostport:
        host, port = hostport.rsplit(':', 1)
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            raise ValueError('SIP URI port must be 1–65535.')

    host = host.strip().lower()
    if not host or not _SIP_HOST_RE.fullmatch(host):
        raise ValueError('SIP URI host is missing or invalid.')

    return raw
