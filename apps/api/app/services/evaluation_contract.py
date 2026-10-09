"""Immutable, integrity-checked evaluation contracts; never substitute the live catalog."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

SNAPSHOT_VERSION = 1


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def freeze_contract(contract: dict[str, Any], *, suite_id: str = '', scenario_id: str = '') -> dict[str, Any]:
    """Called at evaluation creation, not when a later paid review is requested."""
    if not isinstance(contract, dict) or not contract:
        raise ValueError('A nonempty approved evaluation contract is required.')
    value = deepcopy(contract.get('scenario_contract', contract))
    resolved = {}
    if value.get('behavior_preset'):
        from app.integrations.assert_runtime import behavior_preset
        resolved['behavior'] = behavior_preset(value['behavior_preset'])
    if value.get('scenario_preset'):
        from app.integrations.assert_runtime import scenario_preset
        resolved['scenario'] = scenario_preset(value['scenario_preset'])
    body = {'schema_version': SNAPSHOT_VERSION,
            'spec_id': value.get('evaluation_spec_ref') or f'{suite_id}/{scenario_id or value.get("id", "")}',
            'contract': value, 'resolved_presets': resolved}
    return {**body, 'sha256': content_hash(body)}


def recorded_contract(conversation: dict[str, Any]) -> dict[str, Any]:
    findings = conversation.get('evaluation_findings') or {}
    snapshot = findings.get('evaluation_contract_snapshot') if isinstance(findings, dict) else None
    if snapshot is None:
        snapshot = conversation.get('evaluation_contract_snapshot')
    if not isinstance(snapshot, dict) or snapshot.get('schema_version') != SNAPSHOT_VERSION:
        raise ValueError('No versioned evaluation contract snapshot is retained. Explicitly re-evaluate the evidence under a selected contract first.')
    body = {key: value for key, value in snapshot.items() if key != 'sha256'}
    if snapshot.get('sha256') != content_hash(body):
        raise ValueError('The saved evaluation contract snapshot failed its integrity check.')
    value = snapshot.get('contract')
    if not isinstance(value, dict) or not value:
        raise ValueError('The saved evaluation contract is empty or malformed.')
    return {**deepcopy(value), '_resolved_presets': deepcopy(snapshot.get('resolved_presets') or {}),
            '_snapshot_sha256': snapshot['sha256']}
