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
        'name': 'Built-in sample text agent',
        'channel': 'text',
        'target': 'mock_agent',
        'environment': 'local',
        'connection': {},
        'description': 'Predictable sample responses for trying scenarios without contacting a deployed agent.',
        'metadata': {'model_name': 'mock-text', 'prompt_version': 'seed'},
    },
    {
        'id': 'acc-voice-fixture-agent',
        'name': 'Saved cancellation voice replay',
        'channel': 'voice',
        'target': 'voice_fixture',
        'environment': 'local',
        'connection': {},
        'description': 'Explicit saved evidence replay for the cancellation-rescue example; no live target runs.',
        'metadata': {'model_name': 'saved-replay', 'prompt_version': 'fixture-v1'},
    },
    {
        'id': 'generalist-voice-agent',
        'name': 'Built-in generalist voice agent',
        'channel': 'voice',
        'target': 'builtin_sample_voice',
        'environment': 'local',
        'connection': {},
        'description': (
            'Real LLM-backed reference agent for CAE tester-to-agent local audio evaluation. '
            'Requires rtc-asr, Kokoro, and OpenAI API-key or Codex OAuth configuration.'
        ),
        'metadata': {'model_name': 'gpt-5.4-mini', 'prompt_version': 'generalist-v1'},
    },
    {
        'id': 'pipecat-public-demo',
        'name': 'Pipecat public demo',
        'channel': 'voice',
        'target': 'pipecat_public_demo',
        'environment': 'production',
        'connection': {'endpoint_url': 'https://www.pipecat.ai/'},
        'description': (
            'Real external voice target against the public Pipecat demo. CAE joins its ephemeral '
            'Daily room directly, sends current-run tester audio, and captures response media and transcript evidence.'
        ),
        'metadata': {'model_name': 'public-demo-selected-agent', 'prompt_version': 'external'},
    },
    {
        'id': 'holyguacamole-signalwire-agent',
        'name': 'Holy Guacamole SignalWire drive-thru',
        'channel': 'voice',
        'target': 'signalwire_holy_guacamole',
        'environment': 'production',
        'connection': {'endpoint_url': 'https://holyguacamole.signalwire.me/'},
        'description': (
            'Real external SignalWire voice target for the Holy Guacamole drive-thru demo. '
            'CAE drives the public browser WebRTC flow with current-run tester audio and '
            'captures remote media, page events, transcript, timeline, and provenance artifacts.'
        ),
        'metadata': {'model_name': 'signalwire-ai-agent', 'prompt_version': 'external'},
    },
    {
        'id': 'generalist-text-agent',
        'name': 'Built-in generalist text agent',
        'channel': 'text',
        'target': 'openai_codex',
        'environment': 'local',
        'connection': {},
        'description': 'Real generalist text target using OPENAI_API_KEY or connected OpenAI/Codex OAuth.',
        'metadata': {'model_name': 'gpt-5.4-mini', 'prompt_version': 'generalist-v1'},
    },
]

SEED_AGENT_IDS = {str(seed['id']) for seed in SEED_AGENTS}
_SAFE_AGENT_ID_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$')
_NON_NULLABLE_UPDATE_FIELDS = frozenset({'name', 'channel', 'target', 'environment', 'connection', 'metadata'})


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
        now = _now()
        for seed in SEED_AGENTS:
            current = _AGENTS.get(str(seed['id']))
            if current is not None:
                changed = False
                for field in ('name', 'channel', 'target', 'environment', 'connection', 'description', 'metadata'):
                    if current.get(field) != seed.get(field):
                        current[field] = seed.get(field)
                        changed = True
                if changed:
                    current['updated_at'] = now
                    _persist_unlocked(current)
                continue
            record = AgentRecord(
                id=seed['id'],
                name=seed['name'],
                channel=seed['channel'],
                target=seed['target'],
                environment=seed.get('environment') or 'local',
                connection=seed.get('connection') or {},
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
    agent_id = _validate_agent_id((payload.id or _slug_id(payload.name)).strip())
    record = AgentRecord(
        id=agent_id,
        name=payload.name.strip(),
        channel=payload.channel,
        target=payload.target,
        environment=payload.environment,
        connection=payload.connection,
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
    safe_id = _validate_agent_id(agent_id)
    if safe_id in SEED_AGENT_IDS:
        raise ValueError(f'Seed agent cannot be edited: {safe_id}')
    with _LOCK:
        current = _AGENTS.get(safe_id)
        if current is None:
            return None
        next_value = dict(current)
        updates = payload.model_dump(mode='json', exclude_unset=True)
        for key, value in updates.items():
            if value is None and key in _NON_NULLABLE_UPDATE_FIELDS:
                raise ValueError(f'Field "{key}" cannot be null.')
            next_value[key] = value
        next_value['updated_at'] = _now()
        record = AgentRecord.model_validate(next_value)
        data = record.model_dump(mode='json')
        _AGENTS[safe_id] = data
        _persist_unlocked(data)
        return deepcopy(data)


def delete_agent(agent_id: str) -> bool:
    ensure_seeded()
    safe_id = _validate_agent_id(agent_id)
    if safe_id in SEED_AGENT_IDS:
        raise ValueError(f'Seed agent cannot be deleted: {safe_id}')
    with _LOCK:
        if safe_id not in _AGENTS:
            return False
        del _AGENTS[safe_id]
        path = AGENTS_DIR / f'{safe_id}.json'
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
    agent_id = _validate_agent_id(str(record.get('id') or ''))
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = AGENTS_DIR / f'{agent_id}.json'
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _validate_agent_id(agent_id: str) -> str:
    value = (agent_id or '').strip()
    if not value:
        raise ValueError('Agent id is required.')
    if '..' in value or '/' in value or '\\' in value:
        raise ValueError('Agent id contains invalid path characters.')
    if not _SAFE_AGENT_ID_RE.fullmatch(value):
        raise ValueError(
            'Agent id must start with an alphanumeric character and use only '
            'letters, digits, ".", "_", or "-" (max 64 chars).'
        )
    return value


def _slug_id(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:48] or 'agent'
    return f'{slug}-{uuid.uuid4().hex[:6]}'


def _now() -> str:
    return datetime.now(UTC).isoformat()
