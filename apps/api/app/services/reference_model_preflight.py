"""Model-aware preflight and provider routing for built-in generalist targets.

Target and tester are separate participants. Model selections are materialized in
the immutable execution request, every provider needed by an Ollama-backed run is
validated before queueing, and completion calls are routed by the model attached
to each participant rather than by the target's provider alone.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

import httpx

from app.schemas.execution import ExecutionRunCreateRequest
from app.services.agent_store import get_agent
from app.services.reference_generalist_agent import (
    DEFAULT_OLLAMA_GENERALIST_MODEL,
    OLLAMA_MODEL_PREFIX,
    CompletionProvider,
    ReferencePipecatAgentTransport as _BaseReferencePipecatAgentTransport,
    ReferenceRuntimeConfig,
    ReferenceRuntimeError,
    resolve_reference_completion_provider,
)
from app.services.two_agent_pipecat_duplex import build_builtin_sample_voice_graphs


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
        raise ReferenceRuntimeError(f'Built-in generalist {role} model is not configured.')

    if _native_ollama_model(selected) is not None:
        try:
            return probe_ollama_model(selected)
        except ReferenceRuntimeError as exc:
            raise ReferenceRuntimeError(
                f'Built-in generalist {role} model {selected} is not ready: {exc}'
            ) from exc

    try:
        provider = resolve_reference_completion_provider(selected)
        status = provider.status()
    except ReferenceRuntimeError as exc:
        raise ReferenceRuntimeError(
            f'Built-in generalist {role} model {selected} is not ready: {exc}'
        ) from exc
    if status.get('status') != 'connected':
        raise ReferenceRuntimeError(
            f'Built-in generalist {role} model {selected} is not ready: '
            f'{status.get("message") or "provider is disconnected"}.'
        )
    return {
        'provider': str(status.get('provider') or provider.provider_id),
        'model_name': selected,
    }


def _saved_agent_target(payload: ExecutionRunCreateRequest) -> str | None:
    if not payload.agent_id:
        return None
    agent = get_agent(payload.agent_id)
    if not isinstance(agent, dict):
        return None
    target = str(agent.get('target') or '').strip()
    return target or None


def _is_local_generalist_voice(payload: ExecutionRunCreateRequest) -> bool:
    if (
        payload.mode == 'pipecat_webrtc'
        and payload.executor_id == 'cae_local_audio_loop'
    ):
        return True
    return _saved_agent_target(payload) == 'builtin_sample_voice'


def _is_generalist_text(payload: ExecutionRunCreateRequest) -> bool:
    if payload.mode != 'text_callable':
        return False
    if payload.text_callable == 'openai_codex':
        return True
    return _saved_agent_target(payload) == 'openai_codex'


def prepare_execution_reference_models(
    payload: ExecutionRunCreateRequest,
) -> ExecutionRunCreateRequest:
    """Materialize model choices and fail closed before an Ollama-backed run."""
    if _is_local_generalist_voice(payload):
        config = ReferenceRuntimeConfig()
        target_model = (payload.model_name or config.llm_model).strip()
        tester_model = (payload.tester_model_name or config.tester_llm_model).strip()
        normalized = (
            payload
            if payload.tester_model_name == tester_model
            else payload.model_copy(update={'tester_model_name': tester_model})
        )
        if (
            _native_ollama_model(target_model) is not None
            or _native_ollama_model(tester_model) is not None
        ):
            checked: dict[str, dict[str, str]] = {}
            for role, model_name in (
                ('target', target_model),
                ('tester', tester_model),
            ):
                if model_name not in checked:
                    checked[model_name] = _ensure_reference_model_ready(
                        model_name,
                        role=role,
                    )
        return normalized

    if _is_generalist_text(payload) and payload.model_name:
        target_model = payload.model_name.strip()
        tester_model = (payload.tester_model_name or target_model).strip()
        if (
            _native_ollama_model(target_model) is not None
            or _native_ollama_model(tester_model) is not None
        ):
            checked: dict[str, dict[str, str]] = {}
            for role, model_name in (
                ('target', target_model),
                ('tester', tester_model),
            ):
                if model_name not in checked:
                    checked[model_name] = _ensure_reference_model_ready(
                        model_name,
                        role=role,
                    )

    return payload


class _ModelRoutingCompletionProvider:
    """Route each completion call through the provider implied by its model id."""

    def __init__(
        self,
        *,
        default_model: str,
        default_provider: CompletionProvider | None = None,
    ) -> None:
        self.default_model = default_model.strip()
        if not self.default_model:
            raise ReferenceRuntimeError('Reference completion model is not configured.')
        provider = default_provider or resolve_reference_completion_provider(self.default_model)
        self._providers: dict[str, CompletionProvider] = {self.default_model: provider}
        self.provider_id = provider.provider_id

    def _provider_for(
        self,
        model_name: str | None,
    ) -> tuple[str, CompletionProvider]:
        selected = (model_name or self.default_model).strip() or self.default_model
        provider = self._providers.get(selected)
        if provider is None:
            provider = resolve_reference_completion_provider(selected)
            self._providers[selected] = provider
        return selected, provider

    def status(self) -> dict[str, Any]:
        return self._providers[self.default_model].status()

    def complete(self, prompt: str, *, model_name: str | None = None) -> str:
        selected, provider = self._provider_for(model_name)
        return provider.complete(prompt, model_name=selected)

    def complete_with_metrics(
        self,
        prompt: str,
        *,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        selected, provider = self._provider_for(model_name)
        complete_with_metrics = getattr(provider, 'complete_with_metrics', None)
        if callable(complete_with_metrics):
            return complete_with_metrics(prompt, model_name=selected)
        started_at = time.perf_counter()
        text = provider.complete(prompt, model_name=selected)
        return {
            'text': text,
            'ttft_ms': None,
            'total_ms': round((time.perf_counter() - started_at) * 1000, 3),
        }

    def stream_with_metrics(
        self,
        prompt: str,
        *,
        model_name: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        selected, provider = self._provider_for(model_name)
        stream = getattr(provider, 'stream_with_metrics', None)
        if callable(stream):
            yield from stream(prompt, model_name=selected)
            return
        started_at = time.perf_counter()
        text = provider.complete(prompt, model_name=selected)
        yield {'type': 'delta', 'text': text}
        yield {
            'type': 'completed',
            'text': text,
            'ttft_ms': None,
            'total_ms': round((time.perf_counter() - started_at) * 1000, 3),
        }


def _resolve_routed_completion_provider(
    model_name: str | None = None,
) -> CompletionProvider:
    selected = (model_name or ReferenceRuntimeConfig().llm_model).strip()
    provider = resolve_reference_completion_provider(selected)
    return _ModelRoutingCompletionProvider(
        default_model=selected,
        default_provider=provider,
    )


def _voice_graphs(
    *,
    config: ReferenceRuntimeConfig,
    runtime: dict[str, Any],
    target_provider: str,
    tester_provider: str,
) -> tuple[Any, Any]:
    stt_runtime = runtime.get('stt') if isinstance(runtime.get('stt'), dict) else {}
    return build_builtin_sample_voice_graphs(
        tester_llm_provider=tester_provider,
        tester_llm_model=config.tester_llm_model,
        target_llm_provider=target_provider,
        target_llm_model=config.llm_model,
        stt_model=str(stt_runtime.get('model') or 'service-selected'),
        tts_model=config.kokoro_model,
        tester_tts_voice=config.kokoro_tester_voice,
        target_tts_voice=config.kokoro_target_voice,
        llm_mode='real',
    )


class _ModelAwareReferencePipecatAgentTransport(_BaseReferencePipecatAgentTransport):
    """Retain independent target/tester provider readiness and provenance."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        config = self.config
        provider_split_required = (
            _native_ollama_model(config.llm_model) is not None
            or _native_ollama_model(config.tester_llm_model) is not None
        )
        self._model_aware_graphs = self.graphs
        if not provider_split_required:
            return

        target_status = self.completion.status()
        target_provider = str(
            target_status.get('provider') or self.completion.provider_id
        )
        if config.tester_llm_model == config.llm_model:
            tester_provider = target_provider
        else:
            tester_completion = resolve_reference_completion_provider(
                config.tester_llm_model
            )
            tester_status = tester_completion.status()
            if tester_status.get('status') != 'connected':
                raise ReferenceRuntimeError(
                    tester_status.get('message')
                    or 'Reference tester LLM is not connected.'
                )
            tester_provider = str(
                tester_status.get('provider') or tester_completion.provider_id
            )

        tester_graph, target_graph = _voice_graphs(
            config=config,
            runtime=self.runtime,
            target_provider=target_provider,
            tester_provider=tester_provider,
        )
        self._tester_graph = tester_graph
        self._target_graph = target_graph
        self._model_aware_graphs = {
            'tester': tester_graph.as_dict(),
            'target': target_graph.as_dict(),
        }
        self.graphs = self._model_aware_graphs
        self.runtime['llm'] = {
            'provider': target_provider,
            'model': config.llm_model,
            'status': 'ready',
        }
        self.runtime['tester_llm'] = {
            'provider': tester_provider,
            'model': config.tester_llm_model,
            'status': 'ready',
        }

    async def run_duplex_session(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = await super().run_duplex_session(*args, **kwargs)
        if self.graphs is self._model_aware_graphs:
            return result

        # The current Pipecat service reports one legacy provider field for both
        # graphs. Completion callbacks are already routed by model_name; replace
        # only that legacy proof with the independently validated local graph.
        self.graphs = self._model_aware_graphs
        session_id = str(args[0] if args else kwargs.get('session_id') or '')
        state = self._sessions.get(session_id)
        if state is not None:
            state.remote_graphs = self._model_aware_graphs
        proof = result.get('proof')
        if isinstance(proof, dict):
            result['proof'] = {**proof, 'graphs': self._model_aware_graphs}
        return result


def _install_execution_model_routing() -> None:
    """Install routing at the execution boundary without changing callback APIs."""
    from app.services import execution_runner, reference_generalist_agent

    marker = '_reference_model_routing_installed'
    if getattr(execution_runner, marker, False):
        return
    execution_runner.resolve_reference_completion_provider = (
        _resolve_routed_completion_provider
    )
    execution_runner.ReferencePipecatAgentTransport = (
        _ModelAwareReferencePipecatAgentTransport
    )
    reference_generalist_agent.ReferencePipecatAgentTransport = (
        _ModelAwareReferencePipecatAgentTransport
    )
    setattr(execution_runner, marker, True)


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
    """Add configured Ollama target readiness without conflating the tester."""
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
                part
                for part in (
                    existing_detail,
                    f'Configured Ollama path: {exc}',
                )
                if part
            ),
        }

    augmented = {**report, 'dependencies': dependencies}
    augmented['ready'] = all(
        isinstance(item, dict) and bool(item.get('ready'))
        for item in dependencies
    )
    return augmented


_install_execution_model_routing()
