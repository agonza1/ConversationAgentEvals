"""Small, dependency-free helpers for vCon interchange at the CAE boundary."""

from __future__ import annotations

from typing import Any


IETF_VCON_CORE_DRAFT = 'draft-ietf-vcon-vcon-core-04'
IETF_VCON_VERSION = '0.4.0'
IETF_VCON_VENDOR = 'ConversationAgentEvals'
IETF_VCON_PRODUCT = 'CAE Execution'


def vcon_dialog_turns(vcon: dict[str, Any]) -> list[str]:
    """Return readable text turns from legacy and IETF vCon dialog shapes.

    IETF vCon 0.4 text dialogs reference speakers through ``parties`` (a list),
    while CAE's historical record uses one ``party`` index.  Recording dialogs
    have no text body and are deliberately excluded from scoreable transcript.
    """
    parties = vcon.get('parties') if isinstance(vcon.get('parties'), list) else []
    dialog = vcon.get('dialog') if isinstance(vcon.get('dialog'), list) else []
    turns: list[str] = []
    for item in dialog:
        if not isinstance(item, dict):
            continue
        body = item.get('body') or item.get('text') or item.get('transcript')
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
