"""Model-aware preflight for built-in generalist execution targets.

The execution UI exposes one target-model selector. For the local two-agent
voice path, an explicit selection must therefore be applied to both the tester
and evaluated target unless the caller deliberately supplied a separate tester
model. Ollama selections are also checked against the local model inventory so
a run fails before it is queued instead of later inside the media pipeline.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.schemas.execution import ExecutionRunCreateRequest
from app.services.reference_generalist_agent import (
    DEFAULT_OLLAMA_GENERALIST_MODEL,
    OLLAMA_MODEL_PREFIX,
    ReferenceRuntimeConfig,
    ReferenceRuntimeError,
)

_OLLAMA_TAGS_TIMEOUT_SECONDS = 2.0


def _native_ollama_model(model_name: str | None) -> str | None:
    selected = (model_name or '').strip()
    if not selected.lower().startswith(OLLAMA_MODEL_PREFIX):
        return None
    native_model = selected[len(OLLAMA_MODEL_PREFIX):].strip()
    if not native_model:
        raise ReferenceRuntimeError(
            'Ollama target model must include a model tag, for example ollama/gemma2:2b.'
        )
    return native_model


def _ollama_base_url() -> str:
    return (
        os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434').strip().rstrip('/')
        or 'http://localhost:11434'
    )


def _installed_ollama_models(payload: Any) -> set[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get('models'), list):
        raise ReferenceRuntimeError('Ollama /api/tags returned an invalid model inventory.')

    installed: set[str] = set()
    for item in payload['models']:
        if isinstance(item, str) and item.strip():
            installed.add(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        for key in ('name', 'model'):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                installed.add(value.strip())
    return installed


def _ollama_model_is_installed(native_model: str, installed: set[str]) -> bool:
    if native_model in installed:
        return True
    if ':' not in native_model and f'{native_model}:latest' in installed:
        return True
    if native_model.endswith(':latest') and native_model.removesuffix(':latest') in installed:
        return True
    return False


def probe_ollama_model(model_name: str) -> dict[str, str]:
    """Verify that Ollama is reachable and the exact selected model is pulled."""
    native_model = _native_ollama_model(model_name)
    if native_model is None:
        raise ReferenceRuntimeError(f'Expected an ollama/... model id, received {model_name!r}.')

    base_url = _ollama_base_url()
    try:
        response = httpx.get(
            f'{base_url}/api/tags',
            timeout=_OLLAMA_TAGS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        installed = _installed_ollama_models(response.json())
    except ReferenceRuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize local-provider failures
        raise ReferenceRuntimeError(
            f'Ollama is unavailable at {base_url}: {exc}. Start Ollama before launching.'
        ) from exc

    if not _ollama_model_is_installed(native_model, installed):
        raise ReferenceRuntimeError(
            f'Ollama is reachable at {base_url}, but {native_model} is not installed. '
            f'Run `ollama pull {native_model}` before launching.'
        )

    return {
        'provider': 'ollama',
        'model_name': f'{OLLAMA_MODEL_PREFIX}{native_model}',
        'native_model': native_model,
        'base_url': base_url,
    }


def _is_local_generalist_voice(payload: ExecutionRunCreateRequest) -> bool:
    return (
        payload.mode == 'pipecat_webrtc'
        and payload.executor_id == 'cae_local_audio_loop'
    )


def _is_generalist_text(payload: ExecutionRunCreateRequest) -> bool:
    return payload.mode == 'text_callable' and payload.text_callable == 'openai_codex'


def prepare_execution_reference_models(
    payload: ExecutionRunCreateRequest,
) -> ExecutionRunCreateRequest:
    """Normalize voice model selection and preflight selected Ollama models."""
    normalized = payload
    local_voice = _is_local_generalist_voice(payload)

    if local_voice and payload.model_name and not payload.tester_model_name:
        normalized = payload.model_copy(
            update={'tester_model_name': payload.model_name},
        )

    model_names: list[str] = []
    if local_voice:
        config = ReferenceRuntimeConfig()
        model_names.extend((
            normalized.model_name or config.llm_model,
            normalized.tester_model_name or config.tester_llm_model,
        ))
    elif _is_generalist_text(normalized) and normalized.model_name:
        model_names.append(normalized.model_name)

    checked: set[str] = set()
    for model_name in model_names:
        selected = (model_name or '').strip()
        if not selected or selected in checked:
            continue
        checked.add(selected)
        if _native_ollama_model(selected) is not None:
            probe_ollama_model(selected)

    return normalized


def _configured_ollama_model_id() -> str | None:
    configured_llm = os.getenv('REFERENCE_LLM_MODEL', '').strip()
    if configured_llm.lower().startswith(OLLAMA_MODEL_PREFIX):
        return configured_llm

    explicit_ollama = os.getenv('REFERENCE_OLLAMA_MODEL', '').strip()
    ollama_url_configured = bool(os.getenv('OLLAMA_BASE_URL', '').strip())
    if not explicit_ollama and not ollama_url_configured:
        return None
    return f'{OLLAMA_MODEL_PREFIX}{explicit_ollama or DEFAULT_OLLAMA_GENERALIST_MODEL}'


def augment_reference_voice_preflight(report: dict[str, Any]) -> dict[str, Any]:
    """Let generic voice health discover a configured, pulled Ollama fallback.

    The browser requests generic voice health before it has a selected model. If
    the configured OpenAI path is unavailable but local Ollama is configured, this
    replaces only the LLM dependency while preserving Pipecat, ASR, TTS, and token
    checks from the existing reference preflight.
    """
    dependencies = [
        dict(item) if isinstance(item, dict) else item
        for item in report.get('dependencies') or []
    ]
    llm_index = next(
        (
            index
            for index, item in enumerate(dependencies)
            if isinstance(item, dict) and item.get('id') == 'llm'
        ),
        None,
    )
    if llm_index is None:
        return report

    llm_dependency = dependencies[llm_index]
    configured_model = _configured_ollama_model_id()
    explicitly_ollama = bool(
        os.getenv('REFERENCE_LLM_MODEL', '').strip().lower().startswith(OLLAMA_MODEL_PREFIX)
    )
    if llm_dependency.get('ready') and not explicitly_ollama:
        return report
    if configured_model is None:
        return report

    try:
        status = probe_ollama_model(configured_model)
        dependencies[llm_index] = {
            **llm_dependency,
            'ready': True,
            'detail': (
                f'Ollama ready for the built-in tester and target with '
                f'{status["model_name"]}.'
            ),
            'provider': status['provider'],
            'target_model': status['model_name'],
            'tester_model': status['model_name'],
        }
    except ReferenceRuntimeError as exc:
        existing_detail = str(llm_dependency.get('detail') or '').strip()
        dependencies[llm_index] = {
            **llm_dependency,
            'ready': False,
            'detail': ' '.join(
                part for part in (existing_detail, f'Local Ollama: {exc}') if part
            ),
        }

    augmented = {**report, 'dependencies': dependencies}
    augmented['ready'] = all(
        isinstance(item, dict) and bool(item.get('ready'))
        for item in dependencies
    )
    return augmented
