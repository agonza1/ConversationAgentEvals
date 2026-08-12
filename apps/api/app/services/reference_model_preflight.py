"""Model-aware preflight for built-in generalist execution targets.

Target and tester are separate participants. A target-model selection must never
silently replace the tester model, but every provider needed by an Ollama-backed
run must be ready before the run is queued. Ollama selections are also checked
against the local model inventory so failures include the exact pull command.
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
    resolve_reference_completion_provider,
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


def _ensure_reference_model_ready(model_name: str, *, role: str) -> dict[str, str]:
    selected = model_name.strip()
    if not selected:
        raise ReferenceRuntimeError(f'Built-in voice {role} model is not configured.')

    if _native_ollama_model(selected) is not None:
        try:
            return probe_ollama_model(selected)
        except ReferenceRuntimeError as exc:
            raise ReferenceRuntimeError(
                f'Built-in voice {role} model {selected} is not ready: {exc}'
            ) from exc

    try:
        provider = resolve_reference_completion_provider(selected)
        status = provider.status()
    except ReferenceRuntimeError as exc:
        raise ReferenceRuntimeError(
            f'Built-in voice {role} model {selected} is not ready: {exc}'
        ) from exc
    if status.get('status') != 'connected':
        raise ReferenceRuntimeError(
            f'Built-in voice {role} model {selected} is not ready: '
            f'{status.get("message") or "provider is disconnected"}.'
        )
    return {
        'provider': str(status.get('provider') or provider.provider_id),
        'model_name': selected,
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
    """Fail closed for every provider participating in an Ollama-backed run."""
    if _is_local_generalist_voice(payload):
        config = ReferenceRuntimeConfig()
        target_model = (payload.model_name or config.llm_model).strip()
        tester_model = (payload.tester_model_name or config.tester_llm_model).strip()
        if (
            _native_ollama_model(target_model) is not None
            or _native_ollama_model(tester_model) is not None
        ):
            readiness: dict[str, dict[str, str]] = {}
            for role, model_name in (
                ('target', target_model),
                ('tester', tester_model),
            ):
                if model_name not in readiness:
                    readiness[model_name] = _ensure_reference_model_ready(
                        model_name,
                        role=role,
                    )
    elif _is_generalist_text(payload) and payload.model_name:
        selected = payload.model_name.strip()
        if _native_ollama_model(selected) is not None:
            _ensure_reference_model_ready(selected, role='target')

    # Target and tester remain independently configured. The immutable execution
    # snapshot will resolve tester_model_name from ReferenceRuntimeConfig when the
    # request does not carry an explicit tester override.
    return payload


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
    """Add configured Ollama target readiness without conflating the tester.

    The browser requests generic voice health before launching a specific run.
    When the configured primary provider is unavailable but an Ollama target is
    configured, validate both that target and the independently configured tester.
    Exact per-run selections are validated again by prepare_execution_reference_models.
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
    configured_target_model = _configured_ollama_model_id()
    explicitly_ollama = bool(
        os.getenv('REFERENCE_LLM_MODEL', '').strip().lower().startswith(OLLAMA_MODEL_PREFIX)
    )
    if llm_dependency.get('ready') and not explicitly_ollama:
        return report
    if configured_target_model is None:
        return report

    config = ReferenceRuntimeConfig()
    tester_model = config.tester_llm_model
    try:
        target_status = _ensure_reference_model_ready(
            configured_target_model,
            role='target',
        )
        tester_status = (
            target_status
            if tester_model == configured_target_model
            else _ensure_reference_model_ready(tester_model, role='tester')
        )
        if tester_model == configured_target_model:
            detail = (
                f'Ollama ready for the built-in target and tester with '
                f'{target_status["model_name"]}.'
            )
        else:
            detail = (
                f'Target {target_status["model_name"]} via {target_status["provider"]}; '
                f'tester {tester_status["model_name"]} via {tester_status["provider"]} ready.'
            )
        dependencies[llm_index] = {
            **llm_dependency,
            'ready': True,
            'detail': detail,
            'provider': target_status['provider'],
            'target_provider': target_status['provider'],
            'tester_provider': tester_status['provider'],
            'target_model': target_status['model_name'],
            'tester_model': tester_status['model_name'],
        }
    except ReferenceRuntimeError as exc:
        existing_detail = str(llm_dependency.get('detail') or '').strip()
        dependencies[llm_index] = {
            **llm_dependency,
            'ready': False,
            'detail': ' '.join(
                part for part in (existing_detail, f'Configured Ollama path: {exc}') if part
            ),
        }

    augmented = {**report, 'dependencies': dependencies}
    augmented['ready'] = all(
        isinstance(item, dict) and bool(item.get('ready'))
        for item in dependencies
    )
    return augmented
