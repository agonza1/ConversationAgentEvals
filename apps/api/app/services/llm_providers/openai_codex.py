from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from app.services.ssl_util import verified_ssl_context


CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann'
AUTHORIZE_URL = 'https://auth.openai.com/oauth/authorize'
TOKEN_URL = 'https://auth.openai.com/oauth/token'
CODEX_RESPONSES_URL = 'https://chatgpt.com/backend-api/codex/responses'
# Prefer Codex backend models (works with ChatGPT OAuth). Platform /v1/models needs api.model.read.
CODEX_MODELS_URL = 'https://chatgpt.com/backend-api/codex/models'
CODEX_MODELS_CLIENT_VERSION = '0.99.0'
OPENAI_PLATFORM_MODELS_URL = 'https://api.openai.com/v1/models'
CALLBACK_REDIRECT_HOST = 'localhost'
CALLBACK_PORT = 1455
REDIRECT_URI = f'http://{CALLBACK_REDIRECT_HOST}:{CALLBACK_PORT}/auth/callback'
DEFAULT_EXECUTION_MODEL = 'gpt-5.4-mini'
FALLBACK_CHAT_MODELS = (
    'gpt-5.4-mini',
    'gpt-5.4',
    'gpt-5.2',
    'gpt-4.1',
    'gpt-4.1-mini',
    'gpt-4o',
    'o3',
    'o3-mini',
    'o4-mini',
)
SCOPE_MISSING_MODELS_HINT = 'Using built-in model list. Re-connect OpenAI to refresh.'
_CHAT_MODEL_PREFIXES = ('gpt-', 'o1', 'o3', 'o4', 'chatgpt-', 'codex-')
_CHAT_MODEL_EXCLUDE_PARTS = (
    'instruct',
    'embedding',
    'whisper',
    'tts',
    'realtime',
    'audio',
    'image',
    'moderation',
    'davinci',
    'babbage',
    'curie',
    'ada',
    'search',
    'similarity',
    'edit',
    'transcribe',
    'diarize',
    'dall-e',
)


def _callback_bind_host() -> str:
    """Bind address for the ephemeral OAuth callback server.

    The redirect URI stays on localhost:1455, but Docker port publishing requires
    listening on 0.0.0.0 inside the API container.
    """
    return os.getenv('OPENAI_CODEX_CALLBACK_BIND_HOST', '0.0.0.0')


# Keep this aligned with the scopes accepted by the Codex OAuth client. Model
# discovery already prefers the ChatGPT Codex endpoint and has a local fallback;
# requesting the platform-only api.model.read scope makes authorization fail.
SCOPE = 'openid profile email offline_access'
ORIGINATOR = 'conversation-agent-evals'
DEFAULT_MODEL = 'gpt-5.4-mini'
TOKEN_REFRESH_THRESHOLD_SECONDS = 60


class CodexResponseError(RuntimeError):
    def __init__(self, status_code: int, detail: str, *, label: str = 'OpenAI request') -> None:
        super().__init__(f'{label} failed ({status_code}): {detail}')
        self.status_code = status_code
        self.detail = detail


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def default_token_path() -> Path:
    override = os.getenv('OPENAI_CODEX_OAUTH_PATH')
    if override:
        return Path(override).expanduser()
    return _repo_root() / '.local' / 'openai-codex-oauth.json'


def disconnect_marker_path(token_path: Path) -> Path:
    """Tombstone that suppresses ~/.codex/auth.json auto-import after Disconnect."""
    return token_path.with_suffix(token_path.suffix + '.disconnected')


def codex_home_auth_path() -> Path:
    return Path.home() / '.codex' / 'auth.json'


class OpenAICodexProvider:
    provider_id = 'openai'

    def __init__(
        self,
        *,
        token_path: Path | None = None,
        http_post: Any | None = None,
        http_post_json: Any | None = None,
        http_post_stream: Any | None = None,
        now: Any | None = None,
    ) -> None:
        self._token_path = token_path or default_token_path()
        self._http_post = http_post or _http_form_post
        self._http_post_json = http_post_json or _http_json_post
        self._http_post_stream = http_post_stream or _http_json_post_stream
        self._now = now or time.time
        self._lock = threading.Lock()
        self._pending: dict[str, Any] | None = None
        self._server: HTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        self._maybe_import_codex_home()
        tokens = self._load_tokens()
        if not tokens:
            return {
                'id': self.provider_id,
                'provider': 'openai_codex',
                'status': 'disconnected',
                'email': None,
                'account_id': None,
                'message': 'Connect OpenAI (Codex OAuth) to unlock the local LLM judge.',
                'last_error': self._last_error,
            }
        try:
            self.ensure_access_token()
            tokens = self._load_tokens() or tokens
        except Exception as exc:  # noqa: BLE001 - surface auth errors in status
            self._last_error = str(exc)
            return {
                'id': self.provider_id,
                'provider': 'openai_codex',
                'status': 'expired',
                'email': tokens.get('email'),
                'account_id': tokens.get('account_id'),
                'message': 'OpenAI session expired. Reconnect Codex OAuth.',
                'last_error': self._last_error,
            }
        return {
            'id': self.provider_id,
            'provider': 'openai_codex',
            'status': 'connected',
            'email': tokens.get('email'),
            'account_id': tokens.get('account_id'),
            'plan_type': tokens.get('plan_type'),
            'message': 'OpenAI Codex OAuth connected.',
            'last_error': None,
        }

    def start_oauth(self) -> dict[str, Any]:
        verifier, challenge = _generate_pkce()
        state = secrets.token_hex(16)
        with self._lock:
            self._stop_server_locked()
            self._pending = {
                'verifier': verifier,
                'state': state,
                'started_at': self._now(),
            }
            self._last_error = None
            self._start_server_locked()

        params = urllib.parse.urlencode(
            {
                'response_type': 'code',
                'client_id': CLIENT_ID,
                'redirect_uri': REDIRECT_URI,
                'scope': SCOPE,
                'code_challenge': challenge,
                'code_challenge_method': 'S256',
                'id_token_add_organizations': 'true',
                'codex_cli_simplified_flow': 'true',
                'state': state,
                'originator': ORIGINATOR,
            }
        )
        return {
            'authorize_url': f'{AUTHORIZE_URL}?{params}',
            'redirect_uri': REDIRECT_URI,
            'provider': 'openai_codex',
        }

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._stop_server_locked()
            self._pending = None
            if self._token_path.is_file():
                self._token_path.unlink()
            # Suppress ~/.codex/auth.json re-import until the user explicitly reconnects.
            marker = disconnect_marker_path(self._token_path)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                json.dumps(
                    {
                        'disconnected_at': datetime.now(UTC).isoformat(),
                        'suppress_codex_home_import': True,
                    },
                    indent=2,
                ),
                encoding='utf-8',
            )
            try:
                marker.chmod(0o600)
            except OSError:
                pass
            self._last_error = None
        return {'status': 'disconnected', 'provider': 'openai_codex'}

    def ensure_access_token(self) -> str:
        tokens = self._load_tokens()
        if not tokens:
            raise RuntimeError('OpenAI Codex OAuth is not connected.')
        expires_at = float(tokens.get('expires_at') or 0)
        if expires_at - self._now() > TOKEN_REFRESH_THRESHOLD_SECONDS:
            return str(tokens['access_token'])
        return self._refresh_access_token(tokens)

    def list_models(self, *, http_get: Any | None = None) -> dict[str, Any]:
        """List chat-oriented models for the connected OpenAI/Codex OAuth account.

        Tries the Codex backend models list first (works with ChatGPT OAuth), then
        platform GET /v1/models (needs api.model.read). On 403/empty/errors, returns a
        curated fallback list with a soft reconnect hint instead of failing hard.
        """
        status = self.status()
        if status.get('status') != 'connected':
            raise PermissionError(
                status.get('message')
                or 'Connect OpenAI (Codex OAuth) to load models.'
            )
        access = self.ensure_access_token()
        tokens = self._load_tokens() or {}
        account_id = tokens.get('account_id')
        headers = {
            'Authorization': f'Bearer {access}',
            'Content-Type': 'application/json',
            'originator': ORIGINATOR,
        }
        if account_id:
            headers['ChatGPT-Account-Id'] = str(account_id)
        getter = http_get or _http_json_get
        last_error: str | None = None

        candidate_urls = (
            f'{CODEX_MODELS_URL}?client_version={CODEX_MODELS_CLIENT_VERSION}',
            OPENAI_PLATFORM_MODELS_URL,
        )
        for models_url in candidate_urls:
            try:
                payload = getter(models_url, headers=headers)
            except CodexResponseError as exc:
                if exc.status_code == 401:
                    try:
                        access = self._refresh_access_token(tokens)
                        headers['Authorization'] = f'Bearer {access}'
                        payload = getter(models_url, headers=headers)
                    except Exception as refresh_exc:  # noqa: BLE001
                        last_error = str(refresh_exc)
                        continue
                else:
                    last_error = str(exc.detail if hasattr(exc, 'detail') else exc)
                    continue
            except Exception as exc:  # noqa: BLE001 - fall back to curated list
                last_error = str(exc)
                continue

            model_ids = _filter_chat_model_ids(payload)
            if not model_ids:
                last_error = 'Models response did not include usable chat models.'
                continue
            if DEFAULT_EXECUTION_MODEL not in model_ids:
                model_ids.insert(0, DEFAULT_EXECUTION_MODEL)
            return {
                'provider': 'openai_codex',
                'status': 'connected',
                'default_model': DEFAULT_EXECUTION_MODEL,
                'source': 'live',
                'message': None,
                'models': [{'id': model_id} for model_id in model_ids],
            }

        del last_error
        fallback_ids = list(FALLBACK_CHAT_MODELS)
        if DEFAULT_EXECUTION_MODEL not in fallback_ids:
            fallback_ids.insert(0, DEFAULT_EXECUTION_MODEL)
        return {
            'provider': 'openai_codex',
            'status': 'connected',
            'default_model': DEFAULT_EXECUTION_MODEL,
            'source': 'fallback',
            'message': SCOPE_MISSING_MODELS_HINT,
            'warning': SCOPE_MISSING_MODELS_HINT,
            'models': [{'id': model_id} for model_id in fallback_ids],
        }

    def _refresh_access_token(self, tokens: dict[str, Any] | None = None) -> str:
        tokens = tokens or self._load_tokens()
        if not tokens:
            raise RuntimeError('OpenAI Codex OAuth is not connected.')
        refreshed = self._exchange_token(
            {
                'grant_type': 'refresh_token',
                'refresh_token': tokens['refresh_token'],
                'client_id': CLIENT_ID,
            }
        )
        self._save_tokens(refreshed)
        return str(refreshed['access_token'])

    def complete_with_metrics(
        self,
        prompt: str,
        *,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        access = self.ensure_access_token()
        tokens = self._load_tokens() or {}
        account_id = tokens.get('account_id')
        if not account_id:
            raise RuntimeError('Missing ChatGPT account id for Codex Responses.')

        model = (model_name or os.getenv('LLM_JUDGE_MODEL') or DEFAULT_MODEL).strip()
        body = {
            'model': model,
            # The ChatGPT Codex Responses backend rejects persisted responses.
            'store': False,
            # The Codex backend only exposes its Responses surface as SSE.
            'stream': True,
            'input': [
                {
                    'role': 'user',
                    'content': [{'type': 'input_text', 'text': prompt}],
                }
            ],
        }
        headers = {
            'Authorization': f'Bearer {access}',
            'ChatGPT-Account-Id': str(account_id),
            'OpenAI-Beta': 'responses=v1',
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
            'originator': ORIGINATOR,
        }
        request_started_at = time.perf_counter()
        try:
            response = self._http_post_json(CODEX_RESPONSES_URL, body, headers=headers)
        except CodexResponseError as exc:
            if exc.status_code != 401:
                raise
            access = self._refresh_access_token(tokens)
            headers['Authorization'] = f'Bearer {access}'
            request_started_at = time.perf_counter()
            response = self._http_post_json(CODEX_RESPONSES_URL, body, headers=headers)
        text = _extract_responses_text(response)
        if not text:
            raise RuntimeError('Codex Responses returned an empty completion.')
        raw_metrics = response.get('_completion_metrics')
        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        ttft_ms = metrics.get('ttft_ms')
        end_to_end_ttft_ms = (
            (request_started_at - started_at) * 1000 + float(ttft_ms)
            if isinstance(ttft_ms, (int, float))
            else None
        )
        return {
            'text': text,
            'ttft_ms': round(end_to_end_ttft_ms, 3) if end_to_end_ttft_ms is not None else None,
            'total_ms': round((time.perf_counter() - started_at) * 1000, 3),
        }

    def complete(self, prompt: str, *, model_name: str | None = None) -> str:
        return str(self.complete_with_metrics(prompt, model_name=model_name)['text'])

    def stream_with_metrics(
        self,
        prompt: str,
        *,
        model_name: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield Codex Responses text as it arrives instead of buffering SSE."""
        started_at = time.perf_counter()
        access = self.ensure_access_token()
        tokens = self._load_tokens() or {}
        account_id = tokens.get('account_id')
        if not account_id:
            raise RuntimeError('Missing ChatGPT account id for Codex Responses.')
        model = (model_name or os.getenv('LLM_JUDGE_MODEL') or DEFAULT_MODEL).strip()
        body = {
            'model': model,
            'store': False,
            'stream': True,
            'input': [
                {
                    'role': 'user',
                    'content': [{'type': 'input_text', 'text': prompt}],
                }
            ],
        }
        headers = {
            'Authorization': f'Bearer {access}',
            'ChatGPT-Account-Id': str(account_id),
            'OpenAI-Beta': 'responses=v1',
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
            'originator': ORIGINATOR,
        }

        def events() -> Iterator[str]:
            try:
                yield from self._http_post_stream(CODEX_RESPONSES_URL, body, headers=headers)
            except CodexResponseError as exc:
                if exc.status_code != 401:
                    raise
                refreshed = self._refresh_access_token(tokens)
                headers['Authorization'] = f'Bearer {refreshed}'
                yield from self._http_post_stream(CODEX_RESPONSES_URL, body, headers=headers)

        chunks: list[str] = []
        ttft_ms: float | None = None
        for delta in events():
            if not delta:
                continue
            if ttft_ms is None:
                ttft_ms = round((time.perf_counter() - started_at) * 1000, 3)
            chunks.append(delta)
            yield {'type': 'delta', 'text': delta}
        text = ''.join(chunks).strip()
        if not text:
            raise RuntimeError('Codex Responses returned an empty completion.')
        yield {
            'type': 'completed',
            'text': text,
            'ttft_ms': ttft_ms,
            'total_ms': round((time.perf_counter() - started_at) * 1000, 3),
        }

    def complete_oauth_from_callback(self, code: str, state: str | None = None) -> dict[str, Any]:
        """Exchange an authorization code (callback or manual paste). Used by tests."""
        with self._lock:
            pending = dict(self._pending or {})
        if not pending:
            raise RuntimeError('No OAuth login in progress.')
        if state != pending.get('state'):
            raise RuntimeError('OAuth state mismatch.')
        tokens = self._exchange_token(
            {
                'grant_type': 'authorization_code',
                'client_id': CLIENT_ID,
                'code': code,
                'code_verifier': pending['verifier'],
                'redirect_uri': REDIRECT_URI,
            }
        )
        self._save_tokens(tokens)
        with self._lock:
            self._pending = None
            self._stop_server_locked()
        return tokens

    def _start_server_locked(self) -> None:
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != '/auth/callback':
                    self.send_response(404)
                    self.end_headers()
                    return
                params = urllib.parse.parse_qs(parsed.query)
                code = (params.get('code') or [None])[0]
                state = (params.get('state') or [None])[0]
                error = (params.get('error') or [None])[0]
                try:
                    if error:
                        raise RuntimeError(error)
                    if not code:
                        raise RuntimeError('Missing authorization code')
                    provider.complete_oauth_from_callback(code, state)
                    body = b'<html><body><h1>ConversationAgentEvals</h1><p>OpenAI login complete. You can close this tab.</p></body></html>'
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:  # noqa: BLE001
                    provider._last_error = str(exc)
                    message = f'Login failed: {exc}'.encode()
                    self.send_response(400)
                    self.send_header('Content-Type', 'text/plain')
                    self.send_header('Content-Length', str(len(message)))
                    self.end_headers()
                    self.wfile.write(message)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        server = HTTPServer((_callback_bind_host(), CALLBACK_PORT), Handler)
        thread = threading.Thread(target=server.serve_forever, name='openai-codex-oauth', daemon=True)
        self._server = server
        self._server_thread = thread
        thread.start()

    def _stop_server_locked(self) -> None:
        server = self._server
        server_thread = self._server_thread
        self._server = None
        self._server_thread = None
        if server is not None:
            if threading.current_thread() is server_thread:
                # HTTPServer.shutdown() waits for serve_forever() to return, so
                # invoking it from the callback handler thread would deadlock.
                threading.Thread(
                    target=_shutdown_server,
                    args=(server,),
                    name='openai-codex-oauth-shutdown',
                    daemon=True,
                ).start()
                return
            try:
                server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                server.server_close()
            except Exception:  # noqa: BLE001
                pass

    def _exchange_token(self, form: dict[str, str]) -> dict[str, Any]:
        payload = self._http_post(TOKEN_URL, form)
        access = payload.get('access_token')
        refresh = payload.get('refresh_token')
        expires_in = payload.get('expires_in')
        if not access or not refresh or expires_in is None:
            raise RuntimeError('ChatGPT token response missing required fields.')
        identity = decode_chatgpt_identity(str(access))
        if not identity.get('account_id'):
            raise RuntimeError('Failed to extract account id from ChatGPT access token.')
        return {
            'access_token': access,
            'refresh_token': refresh,
            'expires_at': self._now() + float(expires_in),
            'account_id': identity['account_id'],
            'email': identity.get('email'),
            'plan_type': identity.get('plan_type'),
            'id_token': payload.get('id_token'),
        }

    def _load_tokens(self) -> dict[str, Any] | None:
        path = self._token_path
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None
        tokens = raw.get('tokens') if isinstance(raw.get('tokens'), dict) else raw
        if not isinstance(tokens, dict):
            return None
        access = tokens.get('access_token') or tokens.get('access')
        refresh = tokens.get('refresh_token') or tokens.get('refresh')
        account_id = tokens.get('account_id') or tokens.get('accountId')
        if not access or not refresh or not account_id:
            return None
        expires_at = tokens.get('expires_at') or tokens.get('expires')
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00')).timestamp()
            except ValueError:
                expires_at = 0
        return {
            'access_token': access,
            'refresh_token': refresh,
            'account_id': account_id,
            'email': tokens.get('email'),
            'plan_type': tokens.get('plan_type') or tokens.get('planType'),
            'expires_at': float(expires_at or 0),
            'id_token': tokens.get('id_token'),
        }

    def _save_tokens(self, tokens: dict[str, Any]) -> None:
        path = self._token_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'provider': 'openai_codex',
            'auth_mode': 'chatgpt',
            'tokens': {
                'id_token': tokens.get('id_token'),
                'access_token': tokens['access_token'],
                'refresh_token': tokens['refresh_token'],
                'account_id': tokens['account_id'],
                'email': tokens.get('email'),
                'plan_type': tokens.get('plan_type'),
                'expires_at': tokens['expires_at'],
            },
            'last_refresh': datetime.now(UTC).isoformat(),
        }
        path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        try:
            path.chmod(0o600)
        except OSError:
            pass
        marker = disconnect_marker_path(path)
        if marker.is_file():
            try:
                marker.unlink()
            except OSError:
                pass

    def _maybe_import_codex_home(self) -> None:
        if self._token_path.is_file():
            return
        if disconnect_marker_path(self._token_path).is_file():
            return
        if (os.getenv('OPENAI_CODEX_IMPORT_HOME') or '1').strip().lower() in {'0', 'false', 'no', 'off'}:
            return
        source = codex_home_auth_path()
        if not source.is_file():
            return
        try:
            raw = json.loads(source.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return
        tokens = raw.get('tokens') if isinstance(raw.get('tokens'), dict) else raw
        if not isinstance(tokens, dict):
            return
        access = tokens.get('access_token') or tokens.get('access')
        refresh = tokens.get('refresh_token') or tokens.get('refresh')
        account_id = tokens.get('account_id') or tokens.get('accountId')
        if not access or not refresh:
            return
        if not account_id:
            account_id = decode_chatgpt_identity(str(access)).get('account_id')
        if not account_id:
            return
        identity = decode_chatgpt_identity(str(access))
        expires_at = tokens.get('expires_at') or tokens.get('expires') or (self._now() + 3600)
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00')).timestamp()
            except ValueError:
                expires_at = self._now() + 3600
        self._save_tokens(
            {
                'access_token': access,
                'refresh_token': refresh,
                'account_id': account_id,
                'email': tokens.get('email') or identity.get('email'),
                'plan_type': tokens.get('plan_type') or identity.get('plan_type'),
                'expires_at': float(expires_at),
                'id_token': tokens.get('id_token'),
            }
        )


def decode_chatgpt_identity(access_token: str) -> dict[str, str | None]:
    empty = {'account_id': None, 'email': None, 'plan_type': None}
    parts = access_token.split('.')
    if len(parts) != 3:
        return empty
    try:
        padded = parts[1] + ('=' * (-len(parts[1]) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode('utf-8')).decode('utf-8'))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return empty
    auth = payload.get('https://api.openai.com/auth') if isinstance(payload, dict) else None
    profile = payload.get('https://api.openai.com/profile') if isinstance(payload, dict) else None
    auth = auth if isinstance(auth, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    account_id = auth.get('chatgpt_account_id')
    email = profile.get('email')
    plan_type = auth.get('chatgpt_plan_type')
    return {
        'account_id': str(account_id) if account_id else None,
        'email': str(email) if email else None,
        'plan_type': str(plan_type) if plan_type else None,
    }


def _generate_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
    digest = hashlib.sha256(verifier.encode('utf-8')).digest()
    challenge = base64.urlsafe_b64encode(digest).decode('utf-8').rstrip('=')
    return verifier, challenge


def _shutdown_server(server: HTTPServer) -> None:
    try:
        server.shutdown()
    finally:
        server.server_close()


def _http_form_post(url: str, form: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(form).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 - intentional HTTPS OAuth
            request,
            timeout=30,
            context=verified_ssl_context(),
        ) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'ChatGPT token request failed ({exc.code}): {detail}') from exc


def _http_json_post(url: str, body: dict[str, Any], *, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    started_at = time.perf_counter()
    try:
        with urllib.request.urlopen(  # noqa: S310
            request,
            timeout=90,
            context=verified_ssl_context(),
        ) as response:
            headers_map = getattr(response, 'headers', {})
            content_type = headers_map.get('Content-Type', '')
            ttft_ms: float | None = None
            if hasattr(response, 'readline'):
                raw_lines: list[bytes] = []
                while True:
                    raw_line = response.readline()
                    if not raw_line:
                        break
                    raw_lines.append(raw_line)
                    if ttft_ms is None and _sse_line_has_text_delta(raw_line):
                        ttft_ms = round((time.perf_counter() - started_at) * 1000, 3)
                raw = b''.join(raw_lines).decode('utf-8')
            else:
                raw = response.read().decode('utf-8')
            # The Codex backend streams its response, but some proxy paths omit
            # or rewrite Content-Type.  Trust the SSE framing too so a real
            # streamed completion is not accidentally parsed as JSON.
            if (
                'text/event-stream' in content_type.lower()
                or raw.lstrip().startswith(('event:', 'data:'))
            ):
                payload = _parse_responses_sse(raw)
                if ttft_ms is not None:
                    payload['_completion_metrics'] = {'ttft_ms': ttft_ms}
                return payload
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise CodexResponseError(exc.code, detail, label='Codex Responses request') from exc


def _http_json_post_stream(
    url: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str],
) -> Iterator[str]:
    """Yield output-text deltas directly from a Responses API SSE stream."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(  # noqa: S310
            request,
            timeout=90,
            context=verified_ssl_context(),
        ) as response:
            saw_delta = False
            completed_texts: list[str] = []
            for raw_line in response:
                line = raw_line.decode('utf-8', errors='replace')
                if not line.startswith('data:'):
                    continue
                data = line.removeprefix('data:').strip()
                if not data or data == '[DONE]':
                    continue
                event = json.loads(data)
                event_type = str(event.get('type') or '')
                delta = event.get('delta')
                if (
                    event_type == 'response.output_text.delta'
                    and isinstance(delta, str)
                    and delta
                ):
                    saw_delta = True
                    yield delta
                    continue
                text = event.get('text')
                if (
                    event_type == 'response.output_text.done'
                    and isinstance(text, str)
                    and text
                ):
                    completed_texts.append(text)
            if not saw_delta:
                yield from completed_texts
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise CodexResponseError(exc.code, detail, label='Codex Responses request') from exc


def _sse_line_has_text_delta(raw_line: bytes) -> bool:
    line = raw_line.decode('utf-8', errors='replace')
    if not line.startswith('data:'):
        return False
    data = line.removeprefix('data:').strip()
    if not data or data == '[DONE]':
        return False
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(event, dict)
        and event.get('type') == 'response.output_text.delta'
        and isinstance(event.get('delta'), str)
        and bool(event['delta'])
    )


def _http_json_get(url: str, *, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(  # noqa: S310
            request,
            timeout=30,
            context=verified_ssl_context(),
        ) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise CodexResponseError(exc.code, detail, label='OpenAI models request') from exc


def _parse_responses_sse(raw: str) -> dict[str, Any]:
    """Collect a Codex Responses SSE stream into the existing response shape."""
    deltas: list[str] = []
    completed_texts: list[str] = []
    completed_response: dict[str, Any] | None = None
    for line in raw.splitlines():
        if not line.startswith('data:'):
            continue
        data = line.removeprefix('data:').strip()
        if not data or data == '[DONE]':
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get('type') or '')
        response = event.get('response')
        # A stream begins with response.created, whose output is intentionally
        # empty.  Only retain an actual completed response; otherwise its empty
        # initial envelope would mask text sent in later events.
        if event_type == 'response.completed' and isinstance(response, dict):
            completed_response = response
        delta = event.get('delta')
        if event_type == 'response.output_text.delta' and isinstance(delta, str):
            deltas.append(delta)
        # Codex sometimes emits the full value in output_text.done rather than
        # individual delta events.  Prefer that authoritative final text below.
        text = event.get('text')
        if (
            event_type == 'response.output_text.done'
            and isinstance(text, str)
            and text
        ):
            completed_texts.append(text)

    if completed_texts:
        return {'output_text': ''.join(completed_texts)}
    if deltas:
        return {'output_text': ''.join(deltas)}
    if completed_response is not None:
        return completed_response
    raise RuntimeError('Codex Responses stream did not include a completion payload.')


def _is_chat_model_id(model_id: str) -> bool:
    lowered = model_id.strip().lower()
    if not lowered:
        return False
    if any(part in lowered for part in _CHAT_MODEL_EXCLUDE_PARTS):
        return False
    if 'auto-review' in lowered or lowered.endswith('-review'):
        return False
    return any(lowered.startswith(prefix) for prefix in _CHAT_MODEL_PREFIXES)


def _model_id_from_item(item: dict[str, Any]) -> str | None:
    for key in ('id', 'slug', 'model', 'name'):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _filter_chat_model_ids(payload: dict[str, Any]) -> list[str]:
    raw_items = None
    if isinstance(payload, dict):
        # Codex backend returns {models:[...]} with slug; platform API returns {data:[...]}.
        for key in ('models', 'data'):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                raw_items = candidate
                break
    if not isinstance(raw_items, list):
        raw_items = []
    ids: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if item.get('supported_in_api') is False:
            continue
        model_id = _model_id_from_item(item)
        if not model_id or not _is_chat_model_id(model_id):
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        ids.append(model_id)
    ids.sort(key=lambda value: (0 if value == DEFAULT_EXECUTION_MODEL else 1, value.lower()))
    return ids


def _extract_responses_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get('output_text'), str) and payload['output_text'].strip():
        return payload['output_text'].strip()
    chunks: list[str] = []
    for item in payload.get('output') or []:
        if not isinstance(item, dict):
            continue
        for content in item.get('content') or []:
            if not isinstance(content, dict):
                continue
            text = content.get('text') or content.get('value')
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    if chunks:
        return '\n'.join(chunks)
    choices = payload.get('choices')
    if isinstance(choices, list) and choices:
        message = choices[0].get('message') if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get('content'), str):
            return message['content'].strip()
    return ''
