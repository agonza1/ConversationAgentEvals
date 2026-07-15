from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.llm_providers import set_provider_for_tests
from app.services.llm_providers.openai_codex import OpenAICodexProvider, decode_chatgpt_identity
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


def test_llm_judge_blocks_without_provider_and_runs_when_connected():
    set_provider_for_tests('openai', FakeOpenAIProvider(connected=False))
    blocked = client.post('/api/product/judge', json={'plan': 'free', 'report': {'overall_score': 82}})
    assert blocked.status_code == 200
    assert blocked.json()['status'] == 'blocked'
    assert 'Connect OpenAI' in blocked.json()['message']

    set_provider_for_tests('openai', FakeOpenAIProvider(connected=True, completion='Agree with pass. Keep the scenario.'))
    ready = client.post(
        '/api/product/judge',
        json={
            'plan': 'free',
            'report': {
                'suite_id': 'call-center-voice-ai',
                'scenario_id': 'billing-address-change',
                'verdict': 'pass',
                'overall_score': 91,
                'evidence_spans': ['Verified customer identity'],
            },
            'transcript': 'Agent: verified customer identity.',
        },
    )
    assert ready.status_code == 200
    payload = ready.json()
    assert payload['status'] == 'ready'
    assert payload['judge_output'] == 'Agree with pass. Keep the scenario.'
    assert payload['provider'] == 'openai_codex'
    assert payload['evidence_citations'][0] == 'Verified customer identity'


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


def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip('=')
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return f'{header}.{body}.sig'
