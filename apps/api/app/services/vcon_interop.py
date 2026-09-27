"""Small, dependency-free helpers for vCon interchange at the CAE boundary."""

from __future__ import annotations

import base64
import binascii
from typing import Any


IETF_VCON_CORE_DRAFT = 'draft-ietf-vcon-vcon-core-04'
# The draft revision and the vCon container syntax intentionally align here.
IETF_VCON_VERSION = '0.4.0'
IETF_VCON_VENDOR = 'ConversationAgentEvals'
IETF_VCON_PRODUCT = 'CAE Execution'


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
