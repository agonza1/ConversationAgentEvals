"""Small, dependency-free helpers for vCon interchange at the CAE boundary."""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse


IETF_VCON_CORE_DRAFT = 'draft-ietf-vcon-vcon-core-04'
# The draft revision and the vCon container syntax intentionally align here.
IETF_VCON_VERSION = '0.4.0'
IETF_VCON_VENDOR = 'ConversationAgentEvals'
IETF_VCON_PRODUCT = 'CAE Execution'


def is_vcon_timestamp(value: Any) -> bool:
    """Shared timestamp rule for portable vCon export and evidence intake."""
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def is_vcon_sha512(value: Any, *, allow_legacy: bool = True) -> bool:
    """Accept old CAE bare digests only at intake; new tokens name the algorithm."""
    if not isinstance(value, str) or not value.strip():
        return False
    digest = value.strip()
    if digest.startswith('sha512-'):
        digest = digest[7:]
        if not re.fullmatch(r'[A-Za-z0-9_-]{86}', digest):
            return False
    elif not allow_legacy:
        return False
    try:
        decoded = base64.b64decode((digest + '=' * (-len(digest) % 4)).encode('ascii'),
                                   altchars=b'-_', validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error):
        return False
    return len(decoded) == 64 and (allow_legacy or base64.urlsafe_b64encode(decoded).decode('ascii').rstrip('=') == digest)


def vcon_content_hash(data: bytes) -> str:
    return 'sha512-' + base64.urlsafe_b64encode(hashlib.sha512(data).digest()).decode('ascii').rstrip('=')


def normalize_vcon_sha512(value: str) -> str:
    if not is_vcon_sha512(value):
        raise ValueError('Invalid SHA-512 content hash')
    digest = value.strip().removeprefix('sha512-')
    decoded = base64.b64decode((digest + '=' * (-len(digest) % 4)).encode('ascii'), altchars=b'-_', validate=True)
    return 'sha512-' + base64.urlsafe_b64encode(decoded).decode('ascii').rstrip('=')


def vcon_dialog_errors(dialog: Any, *, allow_legacy_hash: bool = True) -> list[str]:
    """Shared semantic rules for CAE's supported portable text/recording subset."""
    if not isinstance(dialog, list):
        return ['dialog must be a list']
    errors: list[str] = []
    for index, item in enumerate(dialog):
        if not isinstance(item, dict):
            errors.append(f'dialog[{index}] must be an object')
            continue
        if 'start' in item and not is_vcon_timestamp(item['start']):
            errors.append(f'dialog[{index}] start must be an RFC 3339 timestamp')
        duration = item.get('duration')
        if 'duration' in item and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
            errors.append(f'dialog[{index}] duration must be a finite non-negative number of seconds')
        if item.get('type') == 'text':
            if not isinstance(item.get('body'), str) or not item['body'].strip():
                errors.append(f'dialog[{index}] text body is required')
            if item.get('mediatype') != 'text/plain':
                errors.append(f'dialog[{index}] text mediatype must be text/plain')
            if item.get('encoding') not in (None, 'none', 'json', 'base64url'):
                errors.append(f'dialog[{index}] text encoding is unsupported')
            elif item.get('encoding') == 'base64url':
                decoded = _dialog_text_body(item)
                if decoded is None or not decoded.strip():
                    errors.append(f'dialog[{index}] text body must decode as non-empty UTF-8 base64url')
        elif item.get('type') == 'recording':
            # The draft permits a placeholder when captured media is not included.
            # It is not a claim that audio was captured or a conversation failed.
            if not any(key in item for key in ('body', 'url', 'encoding', 'content_hash')):
                continue
            if item.get('encoding') == 'base64url' and isinstance(item.get('body'), str):
                try:
                    data = item['body']
                    decoded = base64.b64decode((data + '=' * (-len(data) % 4)).encode('ascii'), altchars=b'-_', validate=True)
                    digest = item.get('content_hash')
                    if is_vcon_sha512(digest) and normalize_vcon_sha512(digest) != vcon_content_hash(decoded):
                        errors.append(f'dialog[{index}] recording content_hash does not match its body')
                except (UnicodeEncodeError, ValueError, binascii.Error):
                    errors.append(f'dialog[{index}] recording body must be base64url')
            else:
                parsed = urlparse(str(item.get('url') or ''))
                if parsed.scheme != 'https':
                    errors.append(f'dialog[{index}] recording URL must use HTTPS')
            # Inline content need not have a hash, but when supplied it must be valid.
            if ('url' in item or 'content_hash' in item) and not is_vcon_sha512(item.get('content_hash'), allow_legacy=allow_legacy_hash):
                errors.append(f'dialog[{index}] recording content_hash must be a sha512- base64url digest')
        else:
            errors.append(f'dialog[{index}] has unsupported type')
    return errors


def vcon_dialog_turns(vcon: dict[str, Any]) -> list[str]:
    """Return readable text turns from legacy and IETF vCon dialog shapes.

    IETF vCon 0.4 text dialogs reference speakers through ``parties`` (a list),
    while CAE's historical record uses one ``party`` index.  Only text dialogs
    are scoreable: recording and other media dialogs can contain inline bodies
    (including base64url-encoded media) and must never become transcript text.
    """
    parties = vcon.get('parties') if isinstance(vcon.get('parties'), list) else []
    dialog = vcon.get('dialog') if isinstance(vcon.get('dialog'), list) else []
    turns: list[str] = []
    for item in dialog:
        if not isinstance(item, dict):
            continue
        dialog_type = item.get('type')
        if dialog_type is not None and dialog_type != 'text':
            continue
        body = _dialog_text_body(item)
        if not isinstance(body, str) or not body.strip():
            continue
        party_refs = item.get('parties')
        if not isinstance(party_refs, list):
            party_refs = [item.get('party')]
        labels = [
            _party_label(parties, reference)
            for reference in party_refs
            if reference is not None
        ]
        speaker = ' / '.join(dict.fromkeys(label for label in labels if label))
        turns.append(f'{speaker}: {body.strip()}' if speaker else body.strip())
    return turns


def _dialog_text_body(item: dict[str, Any]) -> str | None:
    """Return decoded inline text for a text dialog, preserving old CAE input."""
    if 'body' not in item:
        legacy_text = item.get('text') or item.get('transcript')
        return legacy_text if isinstance(legacy_text, str) else None

    body = item.get('body')
    if not isinstance(body, str):
        return None
    encoding = item.get('encoding')
    if encoding in (None, 'none', 'json'):
        return body
    if encoding != 'base64url':
        return None
    try:
        padded = body + ('=' * (-len(body) % 4))
        return base64.b64decode(
            padded.encode('ascii'), altchars=b'-_', validate=True
        ).decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError, ValueError, binascii.Error):
        return None


def _party_label(parties: list[Any], reference: Any) -> str | None:
    if isinstance(reference, bool):
        return None
    if isinstance(reference, int) and 0 <= reference < len(parties):
        party = parties[reference]
        if isinstance(party, dict):
            label = party.get('name') or party.get('role') or party.get('uuid')
            return str(label).strip() if label else None
    if isinstance(reference, dict):
        label = reference.get('name') or reference.get('role') or reference.get('uuid')
        return str(label).strip() if label else None
    if isinstance(reference, str) and reference.strip():
        return reference.strip()
    return None
