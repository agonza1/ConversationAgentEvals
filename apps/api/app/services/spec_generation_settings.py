"""Deployment-local authoring settings; persisted beside the existing OAuth store."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from app.services.llm_providers import get_provider
from app.services.llm_providers.openai_codex import OpenAICodexProvider, default_token_path, effective_codex_model_name

DEFAULT_SPEC_GENERATION_MODEL = 'gpt-5.4-mini'


def settings_path() -> Path:
    return default_token_path().parent / 'spec-generation-settings.json'


def validate_model(model: str | None) -> str | None:
    if model is None:
        return None
    if not isinstance(model, str):
        raise ValueError('Model ID must be a string.')
    model = model.strip()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}', model):
        raise ValueError('Enter a model ID of 1–160 letters, numbers, dots, colons, underscores or hyphens.')
    return model


def generation_settings() -> dict:
    path = settings_path()
    stored = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(stored, dict):
        raise ValueError('Invalid draft-generation settings.')
    selected = validate_model(stored.get('model'))
    default = (os.getenv('SPEC_GENERATION_MODEL') or DEFAULT_SPEC_GENERATION_MODEL).strip() or DEFAULT_SPEC_GENERATION_MODEL
    requested = selected or default
    provider = get_provider('openai')
    connected = provider.status().get('status') == 'connected'
    effective = effective_codex_model_name(requested) if connected and isinstance(provider, OpenAICodexProvider) else requested
    return {
        'model': selected, 'default_model': default, 'effective_model': effective,
        'source': 'console' if selected else 'deployment',
        'provider': 'openai_codex' if connected else 'openai_api_key',
        'scope': 'deployment',
    }


def save_generation_settings(model: str | None) -> dict:
    model = validate_model(model)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps({'model': model}), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return generation_settings()
