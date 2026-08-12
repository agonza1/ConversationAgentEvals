from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.execution import ExecutionRunCreateRequest
from app.services.reference_generalist_agent import ReferenceRuntimeError
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


def test_selected_voice_model_is_reused_for_tester(monkeypatch):
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://ollama.test')
    monkeypatch.setattr(
        'app.services.reference_model_preflight.httpx.get',
        lambda *_args, **_kwargs: _Response({'models': [{'name': 'gemma2:2b'}]}),
    )

    prepared = prepare_execution_reference_models(_voice_request())

    assert prepared.model_name == 'ollama/gemma2:2b'
    assert prepared.tester_model_name == 'ollama/gemma2:2b'


def test_non_ollama_voice_model_preserves_configured_tester(monkeypatch):
    monkeypatch.setenv('REFERENCE_TESTER_LLM_MODEL', 'gpt-4.1-mini')
    payload = _voice_request(model_name='gpt-5.4-mini')

    prepared = prepare_execution_reference_models(payload)

    assert prepared is payload
    assert prepared.tester_model_name is None


def test_explicit_voice_tester_model_is_preserved(monkeypatch):
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://ollama.test')
    requested_urls: list[str] = []

    def fake_get(url, **_kwargs):
        requested_urls.append(url)
        return _Response({'models': [{'name': 'gemma2:2b'}, {'name': 'gemma2:9b'}]})

    monkeypatch.setattr('app.services.reference_model_preflight.httpx.get', fake_get)
    prepared = prepare_execution_reference_models(
        _voice_request(tester_model_name='ollama/gemma2:9b')
    )

    assert prepared.tester_model_name == 'ollama/gemma2:9b'
    assert requested_urls == [
        'http://ollama.test/api/tags',
        'http://ollama.test/api/tags',
    ]


def test_ollama_probe_rejects_model_that_has_not_been_pulled(monkeypatch):
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://ollama.test')
    monkeypatch.setattr(
        'app.services.reference_model_preflight.httpx.get',
        lambda *_args, **_kwargs: _Response({'models': [{'name': 'llama3.2:3b'}]}),
    )

    with pytest.raises(ReferenceRuntimeError, match=r'ollama pull gemma2:2b'):
        probe_ollama_model('ollama/gemma2:2b')


def test_text_generalist_preflights_selected_ollama_model(monkeypatch):
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://ollama.test')
    requested: list[str] = []

    def fake_get(url, **_kwargs):
        requested.append(url)
        return _Response({'models': [{'model': 'gemma2:2b'}]})

    monkeypatch.setattr('app.services.reference_model_preflight.httpx.get', fake_get)
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


def test_execution_route_queues_normalized_ollama_voice_payload(monkeypatch):
    import app.routes.execution as execution_routes

    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://ollama.test')
    monkeypatch.setattr(
        'app.services.reference_model_preflight.httpx.get',
        lambda *_args, **_kwargs: _Response({'models': [{'name': 'gemma2:2b'}]}),
    )
    captured: dict[str, object] = {}

    def fake_start(payload, *, preflight):
        captured['payload'] = payload
        captured['preflight'] = preflight
        return {'execution_run_id': 'exec-ollama-test', 'status': 'queued'}

    monkeypatch.setattr(execution_routes, 'start_execution_run', fake_start)
    monkeypatch.setattr(execution_routes, 'execute_execution_run', lambda *_args: None)

    response = client.post('/api/execution/runs', json=_voice_request().model_dump(mode='json'))

    assert response.status_code == 200, response.text
    queued_payload = captured['payload']
    assert isinstance(queued_payload, ExecutionRunCreateRequest)
    assert queued_payload.model_name == 'ollama/gemma2:2b'
    assert queued_payload.tester_model_name == 'ollama/gemma2:2b'
    assert captured['preflight'] is True


def test_execution_route_rejects_unpulled_ollama_model_before_queue(monkeypatch):
    import app.routes.execution as execution_routes

    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://ollama.test')
    monkeypatch.setattr(
        'app.services.reference_model_preflight.httpx.get',
        lambda *_args, **_kwargs: _Response({'models': [{'name': 'llama3.2:3b'}]}),
    )

    def should_not_queue(*_args, **_kwargs):
        raise AssertionError('execution should not be queued')

    monkeypatch.setattr(execution_routes, 'start_execution_run', should_not_queue)
    response = client.post('/api/execution/runs', json=_voice_request().model_dump(mode='json'))

    assert response.status_code == 400, response.text
    assert 'ollama pull gemma2:2b' in response.json()['detail']


def test_voice_health_uses_configured_ollama_when_primary_llm_is_unavailable(monkeypatch):
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://ollama.test')
    monkeypatch.setenv('REFERENCE_OLLAMA_MODEL', 'gemma2:2b')
    monkeypatch.delenv('REFERENCE_LLM_MODEL', raising=False)
    monkeypatch.setattr(
        'app.services.reference_model_preflight.httpx.get',
        lambda *_args, **_kwargs: _Response({'models': [{'name': 'gemma2:2b'}]}),
    )
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
    assert llm['target_model'] == 'ollama/gemma2:2b'
    assert llm['tester_model'] == 'ollama/gemma2:2b'
    assert 'built-in tester and target' in llm['detail']
    assert llm['setup_url'] == 'https://example.test/setup'


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
