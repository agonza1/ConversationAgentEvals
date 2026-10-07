import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import editable_assert_spec as drafts
from app.services import spec_generation_settings as settings
from app.services.llm_providers import set_provider_for_tests


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'settings_path', lambda: tmp_path / 'settings.json')
    monkeypatch.setenv('SPEC_GENERATION_MODEL', 'gpt-4.1-mini')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('LLM_JUDGE_API_KEY', raising=False)
    class Provider:
        def status(self):
            return {'status': 'disconnected'}
    set_provider_for_tests('openai', Provider())
    yield
    set_provider_for_tests('openai', None)


@pytest.mark.parametrize('auth_status', ['disconnected', 'expired'])
def test_no_credentials_never_reports_api_key_provider(monkeypatch, auth_status):
    class Provider:
        def status(self): return {'status': auth_status}
    set_provider_for_tests('openai', Provider())
    result = TestClient(app).get('/api/specs/generation-settings').json()
    assert result['provider'] == 'unconfigured'
    assert result['available'] is False


@pytest.mark.parametrize('key_variable', ['OPENAI_API_KEY', 'LLM_JUDGE_API_KEY'])
def test_api_key_provider_requires_a_nonblank_key(monkeypatch, key_variable):
    monkeypatch.setenv(key_variable, ' ')
    assert settings.generation_settings()['available'] is False
    monkeypatch.setenv(key_variable, 'test-key')
    assert settings.generation_settings()['provider'] == 'openai_api_key'
    assert settings.generation_settings()['available'] is True


def test_console_selection_persists_and_reset_restores_environment():
    client = TestClient(app)
    assert client.get('/api/specs/generation-settings').json()['effective_model'] == 'gpt-4.1-mini'
    saved = client.patch('/api/specs/generation-settings', json={'model': ' gpt-4.1 '})
    assert saved.status_code == 200
    assert saved.json()['source'] == 'console'
    assert settings.settings_path().read_text() == json.dumps({'model': 'gpt-4.1'})
    assert client.get('/api/specs/generation-settings').json()['effective_model'] == 'gpt-4.1'
    reset = client.patch('/api/specs/generation-settings', json={'model': None})
    assert reset.json()['effective_model'] == 'gpt-4.1-mini'
    assert reset.json()['source'] == 'deployment'


@pytest.mark.parametrize('model', ['', ' ', 'https://evil.example', 'gpt\n4', 'x' * 161])
def test_invalid_ids_cannot_overwrite_setting(model):
    settings.save_generation_settings('gpt-4.1')
    response = TestClient(app).patch('/api/specs/generation-settings', json={'model': model})
    assert response.status_code == 422
    assert settings.generation_settings()['model'] == 'gpt-4.1'


@pytest.mark.parametrize('content', ['{broken', '[]', '{"model": 123}'])
def test_corrupt_settings_fail_clearly_and_can_be_reset(content):
    settings.settings_path().write_text(content)
    client = TestClient(app)
    assert client.get('/api/specs/generation-settings').status_code == 503
    assert client.patch('/api/specs/generation-settings', json={'model': None}).status_code == 200


def test_generation_uses_persisted_model_with_api_key(monkeypatch):
    settings.save_generation_settings('gpt-4.1')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    def complete(prompt, *, api_key, model_name):
        assert model_name == 'gpt-4.1'
        assert api_key == 'test-key'
        return '{}'
    monkeypatch.setattr(drafts, '_complete_with_api_key', complete)
    assert drafts._complete_generation('draft') == ('{}', 'openai_api_key', 'gpt-4.1')


def test_oauth_reports_and_uses_effective_model(monkeypatch):
    from app.services.llm_providers.openai_codex import OpenAICodexProvider
    provider = OpenAICodexProvider()
    monkeypatch.setattr(provider, 'status', lambda: {'status': 'connected', 'provider': 'openai_codex'})
    observed = []
    monkeypatch.setattr(provider, 'complete', lambda prompt, *, model_name: observed.append(model_name) or '{}')
    set_provider_for_tests('openai', provider)
    saved = settings.save_generation_settings('gpt-5.4-mini')
    assert saved['model'] == 'gpt-5.4-mini'
    assert saved['effective_model'] == 'gpt-6-luna'
    assert saved['provider'] == 'openai_codex'
    assert saved['available'] is True
    assert drafts._complete_generation('draft')[2] == 'gpt-6-luna'
    assert observed == ['gpt-6-luna']


@pytest.mark.parametrize('model,has_temperature', [('gpt-4.1-mini', True), ('gpt-6-luna', False), ('o3', False)])
def test_api_payload_respects_reasoning_temperature(monkeypatch, model, has_temperature):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"choices":[{"message":{"content":"{}"}}]}'
    def open_request(request, **kwargs):
        body = json.loads(request.data)
        assert ('temperature' in body) == has_temperature
        return Response()
    monkeypatch.setattr(drafts.urllib.request, 'urlopen', open_request)
    assert drafts._complete_with_api_key('draft', api_key='test', model_name=model) == '{}'
