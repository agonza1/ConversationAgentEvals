from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.execution import ExecutionRunCreateRequest
from app.services.reference_generalist_agent import (
    ReferenceRuntimeConfig,
    ReferenceRuntimeError,
)
from app.services import reference_model_preflight as model_preflight
from app.services.reference_model_preflight import (
    augment_reference_voice_preflight,
    prepare_execution_reference_models,
    probe_ollama_model,
)


client = TestClient(app)


class _Response:
    def __init__(self, payload, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                'request failed',
                request=httpx.Request('GET', 'http://ollama.test/api/tags'),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


class _ConnectedProvider:
    provider_id = 'fake'

    def status(self):
        return {'status': 'connected', 'provider': self.provider_id}


class _RecordingProvider(_ConnectedProvider):
    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        self.calls: list[tuple[str, str | None]] = []

    def complete(self, prompt: str, *, model_name: str | None = None) -> str:
        self.calls.append((prompt, model_name))
        return f'{self.provider_id}:{model_name}:{prompt}'

    def stream_with_metrics(self, prompt: str, *, model_name: str | None = None):
        text = self.complete(prompt, model_name=model_name)
        yield {'type': 'delta', 'text': text}
        yield {'type': 'completed', 'text': text, 'ttft_ms': 1.0, 'total_ms': 2.0}


def _voice_request(**updates) -> ExecutionRunCreateRequest:
    values = {
        'suite_id': 'call-center-voice-ai',
        'scenario_ids': ['cancellation-rescue'],
        'mode': 'pipecat_webrtc',
        'user_id': 'voice-user',
        'project_id': 'voice-project',
        'model_name': 'ollama/gemma2:2b',
    }
    values.update(updates)
    return ExecutionRunCreateRequest(**values)


def _mock_ollama_inventory(monkeypatch, *models: str) -> None:
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://ollama.test')
    monkeypatch.setattr(
        'app.services.reference_model_preflight.httpx.get',
        lambda *_args, **_kwargs: _Response(
            {'models': [{'name': model} for model in models]}
        ),
    )


def test_selected_voice_target_materializes_and_validates_configured_tester(monkeypatch):
    _mock_ollama_inventory(monkeypatch, 'gemma2:2b')
    monkeypatch.setenv('REFERENCE_TESTER_LLM_MODEL', 'gpt-4.1-mini')
    resolved_models: list[str] = []

    def resolve(model_name):
        resolved_models.append(model_name)
        return _ConnectedProvider()

    monkeypatch.setattr(
        model_preflight,
        'resolve_reference_completion_provider',
        resolve,
    )
    payload = _voice_request()
    prepared = prepare_execution_reference_models(payload)

    assert prepared is not payload
    assert prepared.model_name == 'ollama/gemma2:2b'
    assert prepared.tester_model_name == 'gpt-4.1-mini'
    assert resolved_models == ['gpt-4.1-mini']


def test_non_ollama_voice_materializes_configured_tester(monkeypatch):
    monkeypatch.setenv('REFERENCE_TESTER_LLM_MODEL', 'gpt-4.1-mini')
    payload = _voice_request(model_name='gpt-5.4-mini')

    prepared = prepare_execution_reference_models(payload)

    assert prepared is not payload
    assert prepared.tester_model_name == 'gpt-4.1-mini'


def test_explicit_voice_tester_model_is_preserved(monkeypatch):
    _mock_ollama_inventory(monkeypatch, 'gemma2:2b', 'gemma2:9b')
    requested_urls: list[str] = []

    def fake_get(url, **_kwargs):
        requested_urls.append(url)
        return _Response({
            'models': [
                {'name': 'gemma2:2b'},
                {'name': 'gemma2:9b'},
            ]
        })

    monkeypatch.setattr(model_preflight.httpx, 'get', fake_get)
    payload = _voice_request(tester_model_name='ollama/gemma2:9b')
    prepared = prepare_execution_reference_models(payload)

    assert prepared is payload
    assert prepared.tester_model_name == 'ollama/gemma2:9b'
    assert requested_urls == [
        'http://ollama.test/api/tags',
        'http://ollama.test/api/tags',
    ]


def test_ollama_probe_rejects_model_that_has_not_been_pulled(monkeypatch):
    _mock_ollama_inventory(monkeypatch, 'llama3.2:3b')

    with pytest.raises(ReferenceRuntimeError, match=r'ollama pull gemma2:2b'):
        probe_ollama_model('ollama/gemma2:2b')


def test_text_generalist_preflights_selected_ollama_model(monkeypatch):
    _mock_ollama_inventory(monkeypatch, 'gemma2:2b')
    requested: list[str] = []

    def fake_get(url, **_kwargs):
        requested.append(url)
        return _Response({'models': [{'model': 'gemma2:2b'}]})

    monkeypatch.setattr(model_preflight.httpx, 'get', fake_get)
    payload = ExecutionRunCreateRequest(
        suite_id='call-center-voice-ai',
        scenario_ids=['cancellation-rescue'],
        mode='text_callable',
        text_callable='openai_codex',
        agent_id='generalist-text-agent',
        model_name='ollama/gemma2:2b',
        user_id='text-user',
        project_id='text-project',
    )

    assert prepare_execution_reference_models(payload) is payload
    assert requested == ['http://ollama.test/api/tags']


def test_saved_text_agent_preflights_ollama_before_target_resolution(monkeypatch):
    _mock_ollama_inventory(monkeypatch, 'gemma2:2b')
    requested: list[str] = []

    def fake_get(url, **_kwargs):
        requested.append(url)
        return _Response({'models': [{'name': 'gemma2:2b'}]})

    monkeypatch.setattr(model_preflight.httpx, 'get', fake_get)
    monkeypatch.setattr(
        model_preflight,
        'get_agent',
        lambda _agent_id: {'id': 'generalist-text-agent', 'target': 'openai_codex'},
    )
    payload = ExecutionRunCreateRequest(
        suite_id='call-center-voice-ai',
        scenario_ids=['cancellation-rescue'],
        agent_id='generalist-text-agent',
        model_name='ollama/gemma2:2b',
        user_id='text-user',
        project_id='text-project',
    )

    assert payload.text_callable == 'mock_agent'
    assert prepare_execution_reference_models(payload) is payload
    assert requested == ['http://ollama.test/api/tags']


def test_model_router_resolves_target_and_text_tester_independently(monkeypatch):
    target = _RecordingProvider('ollama')
    tester = _RecordingProvider('openai')
    providers = {
        'ollama/gemma2:2b': target,
        'gpt-4.1-mini': tester,
    }
    monkeypatch.setattr(
        model_preflight,
        'resolve_reference_completion_provider',
        lambda model_name: providers[model_name],
    )
    from app.services import execution_runner

    routed = execution_runner.resolve_reference_completion_provider(
        'ollama/gemma2:2b'
    )
    target_text = routed.complete('target prompt', model_name='ollama/gemma2:2b')
    tester_text = routed.complete('tester prompt', model_name='gpt-4.1-mini')

    assert target_text.startswith('ollama:ollama/gemma2:2b')
    assert tester_text.startswith('openai:gpt-4.1-mini')
    assert target.calls == [('target prompt', 'ollama/gemma2:2b')]
    assert tester.calls == [('tester prompt', 'gpt-4.1-mini')]


def test_voice_graphs_keep_target_and_tester_provider_provenance_separate():
    config = ReferenceRuntimeConfig(
        llm_model='ollama/gemma2:2b',
        tester_llm_model='gpt-4.1-mini',
    )

    tester_graph, target_graph = model_preflight._voice_graphs(
        config=config,
        runtime={'stt': {'model': 'base.en'}},
        target_provider='ollama',
        tester_provider='openai_compatible_api_key',
    )

    assert tester_graph.processors[1].provider == 'openai_compatible_api_key'
    assert tester_graph.processors[1].model == 'gpt-4.1-mini'
    assert target_graph.processors[1].provider == 'ollama'
    assert target_graph.processors[1].model == 'ollama/gemma2:2b'


def test_execution_route_queues_materialized_ollama_voice_payload(monkeypatch):
    import app.routes.execution as execution_routes

    _mock_ollama_inventory(monkeypatch, 'gemma2:2b')
    monkeypatch.setenv('REFERENCE_TESTER_LLM_MODEL', 'ollama/gemma2:2b')
    captured: dict[str, object] = {}

    def fake_start(payload, *, preflight):
        captured['payload'] = payload
        captured['preflight'] = preflight
        return {'execution_run_id': 'exec-ollama-test', 'status': 'queued'}

    monkeypatch.setattr(execution_routes, 'start_execution_run', fake_start)
    monkeypatch.setattr(execution_routes, 'execute_execution_run', lambda *_args: None)

    response = client.post(
        '/api/execution/runs',
        json=_voice_request().model_dump(mode='json'),
    )

    assert response.status_code == 200, response.text
    queued_payload = captured['payload']
    assert isinstance(queued_payload, ExecutionRunCreateRequest)
    assert queued_payload.model_name == 'ollama/gemma2:2b'
    assert queued_payload.tester_model_name == 'ollama/gemma2:2b'
    assert captured['preflight'] is True


def test_execution_route_rejects_unpulled_ollama_model_before_queue(monkeypatch):
    import app.routes.execution as execution_routes

    _mock_ollama_inventory(monkeypatch, 'llama3.2:3b')
    monkeypatch.setenv('REFERENCE_TESTER_LLM_MODEL', 'ollama/gemma2:2b')

    def should_not_queue(*_args, **_kwargs):
        raise AssertionError('execution should not be queued')

    monkeypatch.setattr(execution_routes, 'start_execution_run', should_not_queue)
    response = client.post(
        '/api/execution/runs',
        json=_voice_request().model_dump(mode='json'),
    )

    assert response.status_code == 400, response.text
    assert 'ollama pull gemma2:2b' in response.json()['detail']


def test_saved_text_route_rejects_unpulled_model_before_queue(monkeypatch):
    import app.routes.execution as execution_routes

    _mock_ollama_inventory(monkeypatch, 'llama3.2:3b')
    monkeypatch.setattr(
        model_preflight,
        'get_agent',
        lambda _agent_id: {'id': 'generalist-text-agent', 'target': 'openai_codex'},
    )
    monkeypatch.setattr(
        execution_routes,
        'start_execution_run',
        lambda *_args, **_kwargs: pytest.fail('execution should not be queued'),
    )

    response = client.post(
        '/api/execution/runs',
        json={
            'suite_id': 'call-center-voice-ai',
            'scenario_ids': ['cancellation-rescue'],
            'agent_id': 'generalist-text-agent',
            'model_name': 'ollama/gemma2:2b',
            'user_id': 'text-user',
            'project_id': 'text-project',
        },
    )

    assert response.status_code == 400, response.text
    assert 'ollama pull gemma2:2b' in response.json()['detail']


def test_execution_route_rejects_unavailable_independent_tester(monkeypatch):
    import app.routes.execution as execution_routes

    _mock_ollama_inventory(monkeypatch, 'gemma2:2b')
    monkeypatch.setenv('REFERENCE_TESTER_LLM_MODEL', 'gpt-4.1-mini')

    def unavailable(_model_name):
        raise ReferenceRuntimeError('OpenAI is not connected.')

    monkeypatch.setattr(
        model_preflight,
        'resolve_reference_completion_provider',
        unavailable,
    )
    monkeypatch.setattr(
        execution_routes,
        'start_execution_run',
        lambda *_args, **_kwargs: pytest.fail('execution should not be queued'),
    )

    response = client.post(
        '/api/execution/runs',
        json=_voice_request().model_dump(mode='json'),
    )

    assert response.status_code == 400, response.text
    detail = response.json()['detail']
    assert 'tester model gpt-4.1-mini is not ready' in detail
    assert 'OpenAI is not connected' in detail


def test_voice_health_uses_configured_ollama_for_target_and_tester(monkeypatch):
    _mock_ollama_inventory(monkeypatch, 'gemma2:2b')
    monkeypatch.setenv('REFERENCE_LLM_MODEL', 'ollama/gemma2:2b')
    monkeypatch.delenv('REFERENCE_TESTER_LLM_MODEL', raising=False)
    base = {
        'ready': False,
        'llm_mode': 'real',
        'dependencies': [
            {
                'id': 'llm',
                'label': 'Generalist LLM provider',
                'ready': False,
                'detail': 'Configure an LLM provider.',
                'setup_url': 'https://example.test/setup',
            },
            {'id': 'pipecat', 'ready': True, 'detail': 'ready'},
        ],
    }

    report = augment_reference_voice_preflight(base)
    llm = next(item for item in report['dependencies'] if item['id'] == 'llm')

    assert report['ready'] is True
    assert llm['ready'] is True
    assert llm['provider'] == 'ollama'
    assert llm['target_provider'] == 'ollama'
    assert llm['tester_provider'] == 'ollama'
    assert llm['target_model'] == 'ollama/gemma2:2b'
    assert llm['tester_model'] == 'ollama/gemma2:2b'
    assert 'built-in target and tester' in llm['detail']
    assert llm['setup_url'] == 'https://example.test/setup'


def test_voice_health_validates_independent_tester_provider(monkeypatch):
    _mock_ollama_inventory(monkeypatch, 'gemma2:2b')
    monkeypatch.setenv('REFERENCE_OLLAMA_MODEL', 'gemma2:2b')
    monkeypatch.delenv('REFERENCE_LLM_MODEL', raising=False)
    monkeypatch.setenv('REFERENCE_TESTER_LLM_MODEL', 'gpt-4.1-mini')
    monkeypatch.setattr(
        model_preflight,
        'resolve_reference_completion_provider',
        lambda _model_name: _ConnectedProvider(),
    )
    base = {
        'ready': False,
        'llm_mode': 'real',
        'dependencies': [
            {'id': 'llm', 'ready': False, 'detail': 'Primary provider unavailable.'},
            {'id': 'pipecat', 'ready': True, 'detail': 'ready'},
        ],
    }

    report = augment_reference_voice_preflight(base)
    llm = next(item for item in report['dependencies'] if item['id'] == 'llm')

    assert report['ready'] is True
    assert llm['target_model'] == 'ollama/gemma2:2b'
    assert llm['tester_model'] == 'gpt-4.1-mini'
    assert llm['target_provider'] == 'ollama'
    assert llm['tester_provider'] == 'fake'
    assert 'tester gpt-4.1-mini via fake ready' in llm['detail']


def test_voice_health_preserves_ready_openai_when_ollama_is_only_optional(monkeypatch):
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://ollama.test')
    monkeypatch.delenv('REFERENCE_LLM_MODEL', raising=False)
    base = {
        'ready': True,
        'llm_mode': 'real',
        'dependencies': [
            {'id': 'llm', 'ready': True, 'detail': 'OpenAI ready.'},
            {'id': 'pipecat', 'ready': True, 'detail': 'ready'},
        ],
    }

    assert augment_reference_voice_preflight(base) is base
