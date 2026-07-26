from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.llm_providers import set_provider_for_tests
from app.services.llm_providers.openai_codex import (
    OpenAICodexProvider,
    decode_chatgpt_identity,
    disconnect_marker_path,
    _parse_responses_sse,
)
from app.services.product_service import reset_saved_runs_for_tests


client = TestClient(app)


class FakeOpenAIProvider:
    provider_id = 'openai'

    def __init__(self, *, connected: bool = True, completion: str = 'Agree. Evidence supports the verdict.') -> None:
        self.connected = connected
        self.completion = completion
        self.started = False
        self.disconnected = False

    def status(self) -> dict:
        if self.connected:
            return {
                'id': 'openai',
                'provider': 'openai_codex',
                'status': 'connected',
                'email': 'judge@example.com',
                'account_id': 'acct_test',
                'plan_type': 'plus',
                'message': 'OpenAI Codex OAuth connected.',
                'last_error': None,
            }
        return {
            'id': 'openai',
            'provider': 'openai_codex',
            'status': 'disconnected',
            'email': None,
            'account_id': None,
            'message': 'Connect OpenAI (Codex OAuth) to unlock the local LLM judge.',
            'last_error': None,
        }

    def start_oauth(self) -> dict:
        self.started = True
        return {
            'authorize_url': 'https://auth.openai.com/oauth/authorize?client_id=test',
            'redirect_uri': 'http://localhost:1455/auth/callback',
            'provider': 'openai_codex',
        }

    def disconnect(self) -> dict:
        self.disconnected = True
        self.connected = False
        return {'status': 'disconnected', 'provider': 'openai_codex'}

    def ensure_access_token(self) -> str:
        if not self.connected:
            raise RuntimeError('not connected')
        return 'access-token'

    def list_models(self) -> dict:
        if not self.connected:
            raise PermissionError('Connect OpenAI (Codex OAuth) to load models.')
        return {
            'provider': 'openai_codex',
            'status': 'connected',
            'default_model': 'gpt-5.4',
            'models': [{'id': 'gpt-5.4'}, {'id': 'gpt-4.1-mini'}],
        }

    def complete(self, prompt: str) -> str:
        if not self.connected:
            raise RuntimeError('not connected')
        assert 'evidence-grounded' in prompt.lower() or 'ConversationAgentEvals' in prompt
        return self.completion


def setup_function() -> None:
    reset_saved_runs_for_tests()
    set_provider_for_tests('openai', FakeOpenAIProvider(connected=False))


def teardown_function() -> None:
    set_provider_for_tests('openai', None)


def test_openai_provider_status_and_oauth_start_disconnect():
    set_provider_for_tests('openai', FakeOpenAIProvider(connected=False))
    status = client.get('/api/product/providers/openai/status')
    assert status.status_code == 200
    assert status.json()['status'] == 'disconnected'

    listed = client.get('/api/product/providers')
    assert listed.status_code == 200
    assert listed.json()['providers'][0]['id'] == 'openai'

    started = client.post('/api/product/providers/openai/oauth/start')
    assert started.status_code == 200
    assert 'authorize_url' in started.json()

    fake = FakeOpenAIProvider(connected=True)
    set_provider_for_tests('openai', fake)
    connected = client.get('/api/product/providers/openai/status')
    assert connected.json()['status'] == 'connected'
    assert connected.json()['email'] == 'judge@example.com'

    config = client.get('/api/product/config')
    assert config.json()['llm_judge_status'] == 'enabled'

    disconnected = client.post('/api/product/providers/openai/disconnect')
    assert disconnected.status_code == 200
    assert fake.disconnected is True


def test_llm_judge_blocks_without_provider_and_runs_when_connected(tmp_path, monkeypatch):
    from app.services import product_service

    monkeypatch.setattr(product_service, '_judge_spend_path', lambda: tmp_path / 'llm_judge_spend.json')
    set_provider_for_tests('openai', FakeOpenAIProvider(connected=False))
    blocked = client.post('/api/product/judge', json={'plan': 'free', 'report': {'overall_score': 82}})
    assert blocked.status_code == 200
    blocked_payload = blocked.json()
    assert blocked_payload['status'] == 'blocked'
    assert blocked_payload['block_reason'] == 'provider'
    assert 'Connect OpenAI' in blocked_payload['message']

    set_provider_for_tests(
        'openai',
        FakeOpenAIProvider(
            connected=True,
            completion='{"agrees": true, "rationale": "Identity verification is present.", "next_action": "Keep the scenario."}',
        ),
    )
    ready = client.post(
        '/api/product/judge',
        json={
            'plan': 'free',
            'report': {
                'suite_id': 'call-center-voice-ai',
                'scenario_id': 'billing-address-change',
                'verdict': 'pass',
                'overall_score': 91,
                'missing_actions': [],
                'evidence_spans': [{'source': 'transcript', 'text': 'Verified customer identity'}],
                'evidence_citations': [{'kind': 'span', 'text': 'Created support ticket'}],
            },
            'transcript': 'Agent: verified customer identity.',
        },
    )
    assert ready.status_code == 200
    payload = ready.json()
    assert payload['status'] == 'ready'
    assert payload['provider'] == 'openai_codex'
    assert payload['model']
    assert payload['judge_result']['agrees'] is True
    assert 'Identity verification' in payload['judge_result']['rationale']
    assert payload['judge_result']['next_action'] == 'Keep the scenario.'
    assert any('Verified customer identity' in item or 'Created support ticket' in item for item in payload['evidence_citations'])
    assert payload['prompt_preview']
    assert 'Deterministic findings' in payload['prompt_preview']
    assert payload['spend_control']['spent_daily_credits'] == 10
    assert payload['spend_control']['remaining_daily_credits'] == 190

    again = client.post(
        '/api/product/judge',
        json={'plan': 'free', 'report': {'verdict': 'pass', 'overall_score': 91}, 'transcript': 'ok'},
    )
    assert again.json()['spend_control']['spent_daily_credits'] == 20


def test_openai_codex_token_store_exchange_and_complete(tmp_path: Path, monkeypatch):
    token_path = tmp_path / 'openai-codex-oauth.json'
    calls: list[tuple[str, dict]] = []

    def fake_form_post(url: str, form: dict[str, str]) -> dict:
        calls.append((url, form))
        assert form['grant_type'] == 'authorization_code'
        account_payload = {
            'https://api.openai.com/auth': {'chatgpt_account_id': 'acct_123', 'chatgpt_plan_type': 'plus'},
            'https://api.openai.com/profile': {'email': 'demo@example.com'},
        }
        access = _fake_jwt(account_payload)
        return {
            'access_token': access,
            'refresh_token': 'refresh-1',
            'expires_in': 3600,
            'id_token': 'id-1',
        }

    def fake_json_post(url: str, body: dict, *, headers: dict[str, str]) -> dict:
        assert url.endswith('/codex/responses')
        assert headers['Authorization'].startswith('Bearer ')
        assert headers['ChatGPT-Account-Id'] == 'acct_123'
        assert body['store'] is False
        assert body['stream'] is True
        assert body['input'][0]['content'][0]['text'] == 'judge me'
        return {'output_text': 'Judge says pass.'}

    provider = OpenAICodexProvider(token_path=token_path, http_post=fake_form_post, http_post_json=fake_json_post, now=lambda: 1_700_000_000)
    # Skip binding real port 1455 in tests: seed pending state and exchange manually.
    provider._pending = {'verifier': 'verifier', 'state': 'state-1', 'started_at': 1_700_000_000}
    tokens = provider.complete_oauth_from_callback('auth-code', 'state-1')
    assert tokens['account_id'] == 'acct_123'
    assert token_path.is_file()
    assert provider.status()['status'] == 'connected'
    assert provider.complete('judge me') == 'Judge says pass.'
    assert decode_chatgpt_identity(tokens['access_token'])['email'] == 'demo@example.com'


def test_codex_responses_sse_parser_collects_text_deltas():
    payload = _parse_responses_sse(
        'event: response.output_text.delta\n'
        'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
        'event: response.output_text.delta\n'
        'data: {"type":"response.output_text.delta","delta":" world"}\n\n'
        'event: response.completed\n'
        'data: [DONE]\n'
    )
    assert payload == {'output_text': 'Hello world'}


def test_codex_responses_sse_parser_uses_done_text_not_empty_created_response():
    payload = _parse_responses_sse(
        'event: response.created\n'
        'data: {"type":"response.created","response":{"status":"in_progress","output":[]}}\n\n'
        'event: response.output_text.done\n'
        'data: {"type":"response.output_text.done","text":"Final answer"}\n\n'
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}\n\n'
        'data: [DONE]\n'
    )
    assert payload == {'output_text': 'Final answer'}


def test_codex_response_stream_records_actual_first_text_delta(monkeypatch):
    from app.services.llm_providers import openai_codex as mod

    class _TimedStream:
        # The Codex proxy may omit the SSE content type; framing remains authoritative.
        headers: dict[str, str] = {}

        def __init__(self):
            self.lines = iter([
                b'event: response.created\n',
                b'data: {"type":"response.created","response":{"status":"in_progress"}}\n',
                b'event: response.output_text.delta\n',
                b'data: {"type":"response.output_text.delta","delta":"Hello"}\n',
                b'data: [DONE]\n',
            ])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def readline(self):
            return next(self.lines, b'')

    clock = iter([10.0, 10.125])
    monkeypatch.setattr(mod.time, 'perf_counter', lambda: next(clock))
    monkeypatch.setattr(mod.urllib.request, 'urlopen', lambda *args, **kwargs: _TimedStream())

    payload = mod._http_json_post(
        'https://chatgpt.com/backend-api/codex/responses',
        {'input': []},
        headers={'Authorization': 'Bearer t'},
    )

    assert payload['output_text'] == 'Hello'
    assert payload['_completion_metrics']['ttft_ms'] == 125.0


def test_openai_codex_refreshes_expired_access_token(tmp_path: Path):
    token_path = tmp_path / 'openai-codex-oauth.json'
    old_access = _fake_jwt({'https://api.openai.com/auth': {'chatgpt_account_id': 'acct_refresh'}})
    token_path.write_text(
        json.dumps(
            {
                'tokens': {
                    'access_token': old_access,
                    'refresh_token': 'refresh-old',
                    'account_id': 'acct_refresh',
                    'expires_at': 1,
                }
            }
        ),
        encoding='utf-8',
    )

    def fake_form_post(url: str, form: dict[str, str]) -> dict:
        assert form == {
            'grant_type': 'refresh_token',
            'refresh_token': 'refresh-old',
            'client_id': 'app_EMoamEEZ73f0CkXaXp7hrann',
        }
        return {
            'access_token': _fake_jwt(
                {
                    'https://api.openai.com/auth': {
                        'chatgpt_account_id': 'acct_refresh',
                        'chatgpt_plan_type': 'plus',
                    }
                }
            ),
            'refresh_token': 'refresh-new',
            'expires_in': 3600,
        }

    provider = OpenAICodexProvider(token_path=token_path, http_post=fake_form_post, now=lambda: 100)
    refreshed_access = provider.ensure_access_token()

    assert refreshed_access != old_access
    stored = json.loads(token_path.read_text(encoding='utf-8'))['tokens']
    assert stored['refresh_token'] == 'refresh-new'
    assert stored['expires_at'] == 3700


def test_openai_codex_imports_codex_home_auth(tmp_path: Path, monkeypatch):
    token_path = tmp_path / 'cae-oauth.json'
    home_auth = tmp_path / 'codex-auth.json'
    access = _fake_jwt(
        {
            'https://api.openai.com/auth': {'chatgpt_account_id': 'acct_home', 'chatgpt_plan_type': 'pro'},
            'https://api.openai.com/profile': {'email': 'home@example.com'},
        }
    )
    home_auth.write_text(
        json.dumps(
            {
                'tokens': {
                    'access_token': access,
                    'refresh_token': 'refresh-home',
                    'account_id': 'acct_home',
                    'expires_at': 9_999_999_999,
                }
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr('app.services.llm_providers.openai_codex.codex_home_auth_path', lambda: home_auth)
    provider = OpenAICodexProvider(token_path=token_path, now=lambda: 1_700_000_000)
    status = provider.status()
    assert status['status'] == 'connected'
    assert status['account_id'] == 'acct_home'
    assert token_path.is_file()


def test_disconnect_suppresses_codex_home_reimport(tmp_path: Path, monkeypatch):
    token_path = tmp_path / 'cae-oauth.json'
    home_auth = tmp_path / 'codex-auth.json'
    access = _fake_jwt(
        {
            'https://api.openai.com/auth': {'chatgpt_account_id': 'acct_home', 'chatgpt_plan_type': 'pro'},
            'https://api.openai.com/profile': {'email': 'home@example.com'},
        }
    )
    home_auth.write_text(
        json.dumps(
            {
                'tokens': {
                    'access_token': access,
                    'refresh_token': 'refresh-home',
                    'account_id': 'acct_home',
                    'expires_at': 9_999_999_999,
                }
            }
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr('app.services.llm_providers.openai_codex.codex_home_auth_path', lambda: home_auth)
    provider = OpenAICodexProvider(token_path=token_path, now=lambda: 1_700_000_000)

    assert provider.status()['status'] == 'connected'
    assert provider.disconnect()['status'] == 'disconnected'
    assert not token_path.is_file()
    assert disconnect_marker_path(token_path).is_file()

    # Home auth still present, but Disconnect must stick across status polls.
    assert provider.status()['status'] == 'disconnected'
    assert not token_path.is_file()


def test_http_helpers_use_certifi_ssl_context(monkeypatch):
    import ssl
    from pathlib import Path

    import certifi

    from app.services.llm_providers import openai_codex as mod
    from app.services.ssl_util import verified_ssl_context

    assert Path(certifi.where()).is_file()
    assert isinstance(verified_ssl_context(), ssl.SSLContext)

    seen: dict[str, object] = {}

    class _FakeResponse:
        headers: dict[str, str] = {}
        body = b'{"access_token":"t","ok":true}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.body

    def fake_urlopen(request, timeout=None, context=None):
        seen['context'] = context
        seen['timeout'] = timeout
        return _FakeResponse()

    monkeypatch.setattr(mod.urllib.request, 'urlopen', fake_urlopen)
    assert mod._http_form_post('https://auth.openai.com/oauth/token', {'grant_type': 'refresh_token'}) == {
        'access_token': 't',
        'ok': True,
    }
    assert isinstance(seen['context'], ssl.SSLContext)
    assert seen['timeout'] == 30

    seen.clear()
    assert mod._http_json_post(
        'https://chatgpt.com/backend-api/codex/responses',
        {'input': []},
        headers={'Authorization': 'Bearer t'},
    ) == {'access_token': 't', 'ok': True}
    assert isinstance(seen['context'], ssl.SSLContext)
    assert seen['timeout'] == 90

    _FakeResponse.body = (
        b'event: response.output_text.delta\n'
        b'data: {"type":"response.output_text.delta","delta":"streamed"}\n\n'
        b'data: [DONE]\n'
    )
    assert mod._http_json_post(
        'https://chatgpt.com/backend-api/codex/responses',
        {'input': []},
        headers={'Authorization': 'Bearer t'},
    ) == {'output_text': 'streamed'}


def test_openai_models_endpoint_requires_connection():
    set_provider_for_tests('openai', FakeOpenAIProvider(connected=False))
    response = client.get('/api/product/providers/openai/models')
    assert response.status_code == 401
    assert 'Connect OpenAI' in response.json()['detail']


def test_openai_models_endpoint_lists_when_connected():
    set_provider_for_tests('openai', FakeOpenAIProvider(connected=True))
    response = client.get('/api/product/providers/openai/models')
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'connected'
    assert payload['default_model'] == 'gpt-5.4'
    assert {'id': 'gpt-5.4'} in payload['models']


def test_openai_codex_list_models_filters_and_uses_ssl_get(tmp_path: Path):
    from app.services.llm_providers import openai_codex as mod

    token_path = tmp_path / 'oauth.json'
    token_path.write_text(
        json.dumps(
            {
                'access_token': 'access',
                'refresh_token': 'refresh',
                'expires_at': 9_999_999_999,
                'account_id': 'acct_1',
                'email': 'user@example.com',
            }
        ),
        encoding='utf-8',
    )
    provider = OpenAICodexProvider(token_path=token_path, now=lambda: 1_000)

    captured: dict[str, object] = {}

    def fake_get(url: str, *, headers: dict[str, str]):
        captured['url'] = url
        captured['headers'] = headers
        return {
            'models': [
                {'slug': 'gpt-5.4', 'display_name': 'GPT-5.4', 'supported_in_api': True},
                {'slug': 'gpt-5.4-mini', 'display_name': 'GPT-5.4-Mini', 'supported_in_api': True},
                {'slug': 'codex-auto-review', 'display_name': 'Codex Auto Review', 'supported_in_api': True},
                {'id': 'text-embedding-3-large'},
                {'id': 'whisper-1'},
                {'slug': 'o3-mini', 'supported_in_api': True},
            ]
        }

    payload = provider.list_models(http_get=fake_get)
    assert str(captured['url']).startswith(mod.CODEX_MODELS_URL)
    assert f'client_version={mod.CODEX_MODELS_CLIENT_VERSION}' in str(captured['url'])
    assert captured['headers']['Authorization'] == 'Bearer access'
    assert captured['headers']['ChatGPT-Account-Id'] == 'acct_1'
    ids = [item['id'] for item in payload['models']]
    assert ids[0] == 'gpt-5.4-mini'
    assert 'gpt-5.4-mini' in ids
    assert 'o3-mini' in ids
    assert 'codex-auto-review' not in ids
    assert 'text-embedding-3-large' not in ids
    assert 'whisper-1' not in ids
    assert payload.get('source') == 'live'


def test_openai_codex_list_models_falls_back_on_403(tmp_path: Path):
    from app.services.llm_providers import openai_codex as mod

    token_path = tmp_path / 'oauth.json'
    token_path.write_text(
        json.dumps(
            {
                'access_token': 'access',
                'refresh_token': 'refresh',
                'expires_at': 9_999_999_999,
                'account_id': 'acct_1',
                'email': 'user@example.com',
            }
        ),
        encoding='utf-8',
    )
    provider = OpenAICodexProvider(token_path=token_path, now=lambda: 1_000)
    calls: list[str] = []

    def fake_get(url: str, *, headers: dict[str, str]):
        calls.append(url)
        raise mod.CodexResponseError(403, '{"error":{"message":"Missing scopes: api.model.read"}}')

    payload = provider.list_models(http_get=fake_get)
    assert len(calls) == 2
    assert payload['source'] == 'fallback'
    assert 'api.model.read' not in (payload.get('message') or '')
    assert 'Missing scopes' not in (payload.get('message') or '')
    assert 'Could not list OpenAI models' not in (payload.get('message') or '')
    ids = [item['id'] for item in payload['models']]
    assert ids[0] == 'gpt-5.4-mini'
    assert 'gpt-4o' in ids


def test_oauth_authorize_requests_model_read_scope():
    from app.services.llm_providers import openai_codex as mod

    assert 'api.model.read' in mod.SCOPE


def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip('=')
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return f'{header}.{body}.sig'
