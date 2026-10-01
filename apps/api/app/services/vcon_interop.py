"""Small, dependency-free helpers for vCon interchange at the CAE boundary."""

from __future__ import annotations

import base64
import binascii
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


def is_vcon_sha512(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or value.startswith('sha512-'):
        return False
    try:
        digest = value.strip()
        decoded = base64.b64decode((digest + '=' * (-len(digest) % 4)).encode('ascii'),
                                   altchars=b'-_', validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error):
        return False
    return len(decoded) == 64


def vcon_dialog_errors(dialog: Any) -> list[str]:
    """Shared semantic rules for CAE's supported portable text/recording subset."""
    if not isinstance(dialog, list):
        return ['dialog must be a list']
    errors: list[str] = []
    for index, item in enumerate(dialog):
        if not isinstance(item, dict):
            errors.append(f'dialog[{index}] must be an object')
            continue
        if item.get('type') == 'text':
            if not isinstance(item.get('body'), str) or not item['body'].strip():
                errors.append(f'dialog[{index}] text body is required')
            if item.get('mediatype') != 'text/plain':
                errors.append(f'dialog[{index}] text mediatype must be text/plain')
        elif item.get('type') == 'recording':
            if item.get('encoding') == 'base64url' and isinstance(item.get('body'), str):
                try:
                    data = item['body']
                    base64.b64decode((data + '=' * (-len(data) % 4)).encode('ascii'), altchars=b'-_', validate=True)
                except (UnicodeEncodeError, ValueError, binascii.Error):
                    errors.append(f'dialog[{index}] recording body must be base64url')
            else:
                parsed = urlparse(str(item.get('url') or ''))
                if parsed.scheme != 'https':
                    errors.append(f'dialog[{index}] recording URL must use HTTPS')
            if not is_vcon_sha512(item.get('content_hash')):
                errors.append(f'dialog[{index}] recording content_hash must be a base64url SHA-512 digest')
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
