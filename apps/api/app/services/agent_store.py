from __future__ import annotations

import json
import re
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas.agents import AgentCreateRequest, AgentRecord, AgentUpdateRequest


REPO_ROOT = Path(__file__).resolve().parents[4]
AGENTS_DIR = REPO_ROOT / 'artifacts' / 'agents'

_LOCK = threading.Lock()
_AGENTS: dict[str, dict[str, Any]] = {}
_LOADED = False


SEED_AGENTS: list[dict[str, Any]] = [
    {
        'id': 'mock-text-agent',
        'name': 'Mock text agent',
        'channel': 'text',
        'target': 'mock_agent',
        'description': 'Deterministic text callable for sample scenario checks.',
        'metadata': {'model_name': 'mock-text', 'prompt_version': 'seed'},
    },
    {
        'id': 'acc-voice-fixture-agent',
        'name': 'ACC voice fixture agent',
        'channel': 'voice',
        'target': 'voice_fixture',
        'description': 'Offline ACC voice fixture path for cancellation-rescue style runs.',
        'metadata': {'model_name': 'voice-fixture', 'prompt_version': 'seed'},
    },
]


def reset_agents_for_tests(*, clear_files: bool = False) -> None:
    global _LOADED
    with _LOCK:
        _AGENTS.clear()
        _LOADED = False
        if clear_files and AGENTS_DIR.exists():
            for path in AGENTS_DIR.glob('*.json'):
                path.unlink(missing_ok=True)
    ensure_seeded()


def ensure_seeded() -> None:
    _ensure_loaded()
    with _LOCK:
        if _AGENTS:
            return
        now = _now()
        for seed in SEED_AGENTS:
            record = AgentRecord(
                id=seed['id'],
                name=seed['name'],
                channel=seed['channel'],
                target=seed['target'],
                description=seed.get('description'),
                metadata=seed.get('metadata') or {},
                created_at=now,
                updated_at=now,
            )
            data = record.model_dump(mode='json')
            _AGENTS[record.id] = data
            _persist_unlocked(data)


def list_agents() -> list[dict[str, Any]]:
    ensure_seeded()
    with _LOCK:
        rows = [deepcopy(item) for item in _AGENTS.values()]
    rows.sort(key=lambda item: item.get('name') or '')
    return rows


def get_agent(agent_id: str) -> dict[str, Any] | None:
    ensure_seeded()
    with _LOCK:
        value = _AGENTS.get(agent_id)
        return deepcopy(value) if value is not None else None


def create_agent(payload: AgentCreateRequest) -> dict[str, Any]:
    _ensure_loaded()
    now = _now()
    agent_id = (payload.id or _slug_id(payload.name)).strip()
    if not agent_id:
        raise ValueError('Agent id is required.')
    record = AgentRecord(
        id=agent_id,
        name=payload.name.strip(),
        channel=payload.channel,
        target=payload.target,
        description=(payload.description or None),
        metadata=payload.metadata or {},
        created_at=now,
        updated_at=now,
    )
    data = record.model_dump(mode='json')
    with _LOCK:
        if agent_id in _AGENTS:
            raise ValueError(f'Agent already exists: {agent_id}')
        _AGENTS[agent_id] = data
        _persist_unlocked(data)
    return deepcopy(data)


def update_agent(agent_id: str, payload: AgentUpdateRequest) -> dict[str, Any] | None:
    ensure_seeded()
    with _LOCK:
        current = _AGENTS.get(agent_id)
        if current is None:
            return None
        next_value = dict(current)
        updates = payload.model_dump(mode='json', exclude_unset=True)
        for key, value in updates.items():
            next_value[key] = value
        next_value['updated_at'] = _now()
        record = AgentRecord.model_validate(next_value)
        data = record.model_dump(mode='json')
        _AGENTS[agent_id] = data
        _persist_unlocked(data)
        return deepcopy(data)


def delete_agent(agent_id: str) -> bool:
    ensure_seeded()
    with _LOCK:
        if agent_id not in _AGENTS:
            return False
        del _AGENTS[agent_id]
        path = AGENTS_DIR / f'{agent_id}.json'
        path.unlink(missing_ok=True)
        return True


def _ensure_loaded() -> None:
    global _LOADED
    with _LOCK:
        if _LOADED:
            return
        _AGENTS.clear()
        AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        for path in sorted(AGENTS_DIR.glob('*.json')):
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get('id'):
                try:
                    record = AgentRecord.model_validate(payload)
                except Exception:
                    continue
                _AGENTS[record.id] = record.model_dump(mode='json')
        _LOADED = True


def _persist_unlocked(record: dict[str, Any]) -> None:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = AGENTS_DIR / f'{record["id"]}.json'
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _slug_id(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:48] or 'agent'
    return f'{slug}-{uuid.uuid4().hex[:6]}'


def _now() -> str:
    return datetime.now(UTC).isoformat()
