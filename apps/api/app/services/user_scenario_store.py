from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services import benchmark_service

USER_SCENARIOS_SUITE_ID = 'user-scenarios'
USER_SCENARIOS_SUITE_NAME = 'User Scenarios'
STORE_VERSION = 1

_API_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[4]
_LEGACY_STORE_PATH = _API_DIR / 'data' / 'user_scenarios.json'


def default_store_path() -> Path:
    """Durable store under the Compose-mounted `storage/` volume.

    Docker only bind-mounts `/workspace/storage` and `/.local`, so keeping the
    library under `apps/api/data/` loses scenarios on container recreate.
    """
    override = os.getenv('USER_SCENARIOS_PATH')
    if override:
        return Path(override).expanduser()
    return _REPO_ROOT / 'storage' / 'user_scenarios.json'


_DEFAULT_STORE_PATH = default_store_path()

_LOCK = threading.Lock()
_STORE_PATH = _DEFAULT_STORE_PATH
_RECORDS: dict[str, dict[str, Any]] = {}
_LOADED = False


def configure_store_path(path: Path | None) -> None:
    """Point persistence at a temp file (tests) or restore the default path."""
    global _STORE_PATH, _LOADED
    with _LOCK:
        _STORE_PATH = path or default_store_path()
        _LOADED = False
        _RECORDS.clear()
    _ensure_loaded()
    sync_catalog()


def reset_user_scenarios_for_tests() -> None:
    with _LOCK:
        _RECORDS.clear()
        if _STORE_PATH.exists():
            _STORE_PATH.unlink()
        _LOADED = True
    sync_catalog()


def list_user_scenarios() -> list[dict[str, Any]]:
    _ensure_loaded()
    with _LOCK:
        records = sorted(_RECORDS.values(), key=lambda item: str(item.get('created_at') or ''), reverse=True)
        return [deepcopy(_public_scenario(record)) for record in records]


def get_user_scenario(scenario_id: str) -> dict[str, Any] | None:
    _ensure_loaded()
    with _LOCK:
        record = _RECORDS.get(scenario_id)
        return deepcopy(_public_scenario(record)) if record else None


def create_user_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    simulated_user_prompt = _required_text(payload, 'simulated_user_prompt', 'simulatedUserPrompt', 'prompt')
    expected_output = _required_text(payload, 'expected_output', 'expectedOutput')
    description = _required_text(payload, 'description')
    title = _optional_text(payload, 'title') or _title_from_text(description or simulated_user_prompt)
    scenario_id = _optional_text(payload, 'id') or _slug_id(title)

    now = datetime.now(UTC).isoformat()
    record = _to_catalog_scenario(
        {
            'id': scenario_id,
            'suite_id': USER_SCENARIOS_SUITE_ID,
            'title': title,
            'type': 'scenario',
            'simulated_user_prompt': simulated_user_prompt,
            'expected_output': expected_output,
            'description': description,
            'source': 'user_created',
            'created_at': now,
            'updated_at': now,
        }
    )

    _ensure_loaded()
    with _LOCK:
        if scenario_id in _RECORDS:
            raise ValueError(f'Scenario already exists: {scenario_id}')
        _RECORDS[scenario_id] = record
        _persist_unlocked()

    sync_catalog()
    return deepcopy(_public_scenario(record))


def sync_catalog() -> None:
    """Register the user-scenarios suite + scenarios into the live benchmark catalog."""
    _ensure_loaded()
    with _LOCK:
        scenarios = [deepcopy(record) for record in _RECORDS.values()]

    suite = {
        'id': USER_SCENARIOS_SUITE_ID,
        'name': USER_SCENARIOS_SUITE_NAME,
        'provider': 'User',
        'description': 'Scenarios created from the AgentBench UI (file-backed local store).',
        'scenarios': scenarios,
    }
    benchmark_service._SUITES_BY_ID[USER_SCENARIOS_SUITE_ID] = suite
    for key in list(benchmark_service._SCENARIOS_BY_ID):
        if key[0] == USER_SCENARIOS_SUITE_ID:
            del benchmark_service._SCENARIOS_BY_ID[key]
    for scenario in scenarios:
        benchmark_service._SCENARIOS_BY_ID[(USER_SCENARIOS_SUITE_ID, scenario['id'])] = scenario


def ensure_user_scenarios_registered() -> None:
    sync_catalog()


def _ensure_loaded() -> None:
    global _LOADED
    with _LOCK:
        if _LOADED:
            return
        _RECORDS.clear()
        _maybe_migrate_legacy_store_unlocked()
        if _STORE_PATH.exists():
            try:
                payload = json.loads(_STORE_PATH.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                payload = {}
            records = payload.get('scenarios') if isinstance(payload, dict) else None
            if isinstance(records, list):
                for item in records:
                    if isinstance(item, dict) and item.get('id'):
                        _RECORDS[str(item['id'])] = _to_catalog_scenario(item)
        _LOADED = True


def _maybe_migrate_legacy_store_unlocked() -> None:
    """Copy apps/api/data/user_scenarios.json into storage/ once if needed."""
    if _STORE_PATH.exists() or not _LEGACY_STORE_PATH.exists():
        return
    if _STORE_PATH.resolve() == _LEGACY_STORE_PATH.resolve():
        return
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_LEGACY_STORE_PATH, _STORE_PATH)
    except OSError:
        # Fall through: load may still succeed from an empty store.
        return


def _persist_unlocked() -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'version': STORE_VERSION,
        'suite_id': USER_SCENARIOS_SUITE_ID,
        'scenarios': sorted(_RECORDS.values(), key=lambda item: str(item.get('created_at') or '')),
    }
    _STORE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _to_catalog_scenario(raw: dict[str, Any]) -> dict[str, Any]:
    simulated_user_prompt = (
        _optional_text(raw, 'simulated_user_prompt', 'simulatedUserPrompt', 'prompt')
        or _optional_text(raw, 'persona')
        or ''
    )
    expected_output = (
        _optional_text(raw, 'expected_output', 'expectedOutput')
        or _optional_text(raw, 'expected_final_state')
        or ''
    )
    description = _optional_text(raw, 'description') or simulated_user_prompt
    title = _optional_text(raw, 'title') or _title_from_text(description or simulated_user_prompt)
    scenario_id = _optional_text(raw, 'id') or _slug_id(title)

    required_actions = raw.get('required_actions')
    if not isinstance(required_actions, list) or not required_actions:
        required_actions = [
            'acknowledge the simulated user request',
            'gather relevant details',
            'produce the expected outcome',
        ]

    forbidden_actions = raw.get('forbidden_actions')
    if not isinstance(forbidden_actions, list):
        forbidden_actions = []

    rubric = raw.get('rubric')
    if not isinstance(rubric, list) or not rubric:
        keywords = [token for token in re.findall(r'[a-zA-Z]{4,}', expected_output.lower())[:8]]
        rubric = [
            {
                'name': 'expected_outcome',
                'weight': 100,
                'keywords': keywords or ['expected', 'outcome'],
            }
        ]

    return {
        'id': scenario_id,
        'suite_id': USER_SCENARIOS_SUITE_ID,
        'title': title,
        'type': 'scenario',
        'description': description,
        'simulated_user_prompt': simulated_user_prompt,
        'expected_output': expected_output,
        'persona': simulated_user_prompt,
        'goal': description or simulated_user_prompt,
        'prompt': simulated_user_prompt,
        'required_actions': list(required_actions),
        'forbidden_actions': list(forbidden_actions),
        'expected_final_state': expected_output,
        'rubric': deepcopy(rubric),
        'source': _optional_text(raw, 'source') or 'user_created',
        'created_at': _optional_text(raw, 'created_at') or datetime.now(UTC).isoformat(),
        'updated_at': _optional_text(raw, 'updated_at') or datetime.now(UTC).isoformat(),
    }


def _public_scenario(record: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': record['id'],
        'suite_id': record.get('suite_id') or USER_SCENARIOS_SUITE_ID,
        'title': record['title'],
        'type': 'scenario',
        'description': record.get('description') or '',
        'simulated_user_prompt': record.get('simulated_user_prompt') or record.get('prompt') or '',
        'expected_output': record.get('expected_output') or record.get('expected_final_state') or '',
        'persona': record.get('persona'),
        'goal': record.get('goal'),
        'required_actions': deepcopy(record.get('required_actions') or []),
        'forbidden_actions': deepcopy(record.get('forbidden_actions') or []),
        'expected_final_state': record.get('expected_final_state'),
        'rubric': deepcopy(record.get('rubric') or []),
        'source': record.get('source') or 'user_created',
        'created_at': record.get('created_at'),
        'updated_at': record.get('updated_at'),
    }


def _required_text(payload: dict[str, Any], *keys: str) -> str:
    value = _optional_text(payload, *keys)
    if not value:
        raise ValueError(f'{keys[0]} is required')
    return value


def _optional_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _title_from_text(text: str) -> str:
    cleaned = ' '.join(text.strip().split())
    if not cleaned:
        return 'Untitled scenario'
    if len(cleaned) <= 72:
        return cleaned
    return cleaned[:69].rstrip() + '...'


def _slug_id(title: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    slug = slug[:48].strip('-') or 'scenario'
    return f'{slug}-{uuid.uuid4().hex[:8]}'
