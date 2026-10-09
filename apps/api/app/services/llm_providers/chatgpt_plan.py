"""Local OSS ChatGPT-plan OAuth. Never imports Codex credentials or API keys."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from filelock import FileLock

from app.services.llm_providers.openai_codex import _generate_pkce, default_token_path
from app.services.ssl_util import verified_ssl_context

ISSUER = 'https://auth.openai.com'
AUTHORIZE_URL = f'{ISSUER}/api/accounts/authorize'
TOKEN_URL = f'{ISSUER}/api/accounts/oauth/token'
JWKS_URL = f'{ISSUER}/.well-known/jwks.json'
DISCOVERY_URL = f'{ISSUER}/.well-known/openid-configuration'
RESOURCE = 'https://api.openai.com/v1'
PLAN_SCOPE = 'chatgpt.tokens.use.direct'
SCOPES = f'openid profile email offline_access resource.invoke {PLAN_SCOPE}'
MODEL_PREFIX = 'chatgpt_plan/'
CALLBACK_PORT = 1456  # Independent of the legacy Codex execution callback.
REDIRECT_URI = f'http://127.0.0.1:{CALLBACK_PORT}/auth/callback'


class ChatGPTPlanError(RuntimeError):
    """Safe, credential-free error suitable for the local UI and CLI artifacts."""


def local_enabled() -> bool:
    return (os.getenv('CHATGPT_PLAN_LOCAL_ENABLED', '0').lower() in {'1', 'true', 'yes'}
            and os.getenv('APP_ENV', 'development').lower() != 'production')


def store_path() -> Path:
    return default_token_path().parent / 'chatgpt-plan.json'


def validate_model(model: str) -> str:
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', model):
        raise ChatGPTPlanError('Select an account-supported text model ID.')
    return model


class ChatGPTPlanProvider:
    def __init__(self, *, path: Path | None = None, client: Any = None, now: Any = None,
                 verify_identity: Any = None) -> None:
        self.path = path or store_path()
        self.client = client or httpx.Client(verify=verified_ssl_context(), timeout=30, follow_redirects=False)
        self.now = now or time.time
        self.verify_identity = verify_identity or self._verify_identity
        self._pending: dict | None = None
        self._mutex = threading.RLock()
        self._server: ThreadingHTTPServer | None = None
        self._timer: threading.Timer | None = None
        self._last_error: str | None = None
        self._generation = 0

    def _locked(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return FileLock(str(self.path) + '.lock', timeout=35)

    def _load(self) -> dict:
        if not self.path.exists():
            return {'host_id': f'urn:uuid:{uuid.uuid4()}', 'profiles': {}, 'active_profile_id': None}
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or not isinstance(value.get('profiles'), dict) or not value.get('host_id'):
                raise ValueError
            return value
        except (ValueError, OSError) as exc:
            raise ChatGPTPlanError('ChatGPT connection storage is unavailable; repair it before reconnecting.') from exc

    def _save(self, value: dict) -> None:
        temporary = self.path.with_name(f'.{self.path.name}.{uuid.uuid4().hex}.tmp')
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream)
            temporary.replace(self.path)
            self.path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _active(value: dict) -> dict:
        profile = value['profiles'].get(value.get('active_profile_id'))
        return profile if isinstance(profile, dict) else {}

    def status(self) -> dict:
        """Read only: no token refresh, network request, credential import, or inference."""
        if not local_enabled():
            return {'enabled': False, 'status': 'disabled', 'profiles': [], 'judge_model': self.selected_model(),
                    'message': 'Enable CHATGPT_PLAN_LOCAL_ENABLED=1 on a loopback-only local CAE deployment.'}
        with self._locked():
            value = self._load()
        profile = self._active(value)
        has_tokens = bool(profile.get('access_token') and profile.get('refresh_token'))
        sharing = has_tokens and {PLAN_SCOPE, 'resource.invoke'}.issubset(set(profile.get('scopes', [])))
        expired = has_tokens and float(profile.get('expires_at', 0)) <= self.now()
        return {'enabled': True, 'status': 'expired' if expired else 'connected' if has_tokens else 'disconnected',
                'pending': self._pending is not None,
                'sharing': sharing, 'refreshable': bool(profile.get('refresh_token')),
                'active_profile_id': value.get('active_profile_id'),
                'account_binding_sha256': hashlib.sha256(json.dumps([value['host_id'], profile.get('client_id'),
                                                                     profile.get('subject')]).encode()).hexdigest(),
                'email': profile.get('email'), 'judge_model': self._selected_model(value),
                'profiles': [{'id': key, 'label': f"{row.get('email') or 'ChatGPT account'} · {key[:8]}"}
                             for key, row in value['profiles'].items() if row.get('subject')],
                'message': self._last_error or ('ChatGPT plan use authorized.' if sharing else
                    'Continue with ChatGPT and authorize plan use; identity sign-in alone cannot run a judge.')}

    def binding(self) -> str:
        with self._locked():
            value = self._load()
        profile = self._active(value)
        # Stable across token refresh; changes on account/workspace/host switch.
        return hashlib.sha256(json.dumps([value['host_id'], profile.get('client_id'),
                                          profile.get('subject')]).encode()).hexdigest()

    @classmethod
    def _selected_model(cls, value: dict) -> str | None:
        if value.get('judge_mode') != 'chatgpt_plan':
            return None
        return cls._active(value).get('judge_model') or MODEL_PREFIX + 'select-model'

    def selected_model(self) -> str | None:
        # Disabling plan execution does not authorize a change of billing mode.
        # Avoid creating storage at all on installations without a saved choice.
        if not self.path.exists():
            return None
        with self._locked():
            value = self._load()
        return self._selected_model(value)

    def start_oauth(self, profile_id: str | None = None) -> dict:
        self._require_local()
        verifier, challenge = _generate_pkce()
        with self._mutex, self._locked():
            value = self._load()
            profile = value['profiles'].get(profile_id, {}) if profile_id else {}
            if profile_id and not profile:
                raise ChatGPTPlanError('Select an existing ChatGPT account or add a new account.')
            self._generation += 1
            client_id = profile.get('client_id') or value.get('pending_client_id') or 'dynamic_agent_client'
            self._pending = {'state': secrets.token_urlsafe(32), 'nonce': secrets.token_urlsafe(32),
                             'verifier': verifier, 'expires_at': self.now() + 600,
                             'client_id': client_id, 'profile_id': profile_id,
                             'scope': SCOPES,
                             'subject': profile.get('subject'), 'generation': self._generation}
            self._save(value)  # Persist the host ID before the browser is opened.
            try:
                self._start_listener()
            except OSError as exc:
                self._pending = None
                raise ChatGPTPlanError(f'ChatGPT callback port {CALLBACK_PORT} is busy.') from exc
            params = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': REDIRECT_URI,
                      'scope': self._pending['scope'], 'resource': RESOURCE, 'state': self._pending['state'],
                      'nonce': self._pending['nonce'], 'code_challenge': challenge,
                      'code_challenge_method': 'S256', 'ext_agent_host_id': value['host_id']}
            if client_id == 'dynamic_agent_client':
                params['agent_name_hint'] = 'ConVoice QA'
            elif profile.get('id_token'):
                params['id_token_hint'] = profile['id_token']
            # User explicitly requested connection/reconnection, including after declined plan use.
            params['prompt'] = 'consent'
            self._last_error = None
            return {'authorize_url': f'{AUTHORIZE_URL}?{urlencode(params)}', 'redirect_uri': REDIRECT_URI}

    def complete_callback(self, query: dict[str, list[str]]) -> None:
        with self._mutex:
            pending = self._pending
            state = query.get('state', [''])[0]
            if not pending or self.now() > pending['expires_at'] or not secrets.compare_digest(state, pending['state']):
                raise ChatGPTPlanError('ChatGPT sign-in state expired or did not match. Start again.')
            self._pending = None  # One-time consumption, including denied/failed exchanges.
        if query.get('error'):
            raise ChatGPTPlanError('ChatGPT authorization was declined. No account connection was replaced.')
        code = query.get('code', [''])[0]
        issued = query.get('client_id', [''])[0]
        client_id = pending['client_id']
        if client_id == 'dynamic_agent_client':
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', issued) or issued == 'dynamic_agent_client':
                raise ChatGPTPlanError('ChatGPT registration did not return an issued client ID.')
            client_id = issued
            with self._locked():
                value = self._load()
                value['pending_client_id'] = client_id
                self._save(value)
        elif issued and issued != client_id:
            raise ChatGPTPlanError('ChatGPT callback changed the selected account registration.')
        if not code:
            raise ChatGPTPlanError('ChatGPT callback did not include an authorization code.')
        payload = self._post_token({'grant_type': 'authorization_code', 'client_id': client_id,
                                   'code': code, 'code_verifier': pending['verifier'],
                                   'redirect_uri': REDIRECT_URI, 'resource': RESOURCE})
        try:
            identity = self.verify_identity(payload.get('id_token'), client_id, pending['nonce'])
        except Exception as exc:
            raise ChatGPTPlanError('ChatGPT ID token validation failed. The active account was not changed.') from exc
        subject = identity.get('sub')
        if not subject or (pending.get('subject') and subject != pending['subject']):
            raise ChatGPTPlanError('ChatGPT sign-in does not match the selected account.')
        if not payload.get('access_token') or not payload.get('refresh_token'):
            raise ChatGPTPlanError('ChatGPT sign-in did not return a renewable session.')
        profile_id = hashlib.sha256(f'{client_id}:{subject}'.encode()).hexdigest()
        with self._mutex, self._locked():
            if pending['generation'] != self._generation or self.now() > pending['expires_at']:
                raise ChatGPTPlanError('ChatGPT sign-in was cancelled or expired. Start again.')
            value = self._load()
            previous = value['profiles'].get(profile_id, {})
            value['profiles'][profile_id] = {**previous, 'client_id': client_id, 'subject': subject,
                'email': identity.get('email'), 'id_token': payload['id_token'],
                'access_token': payload['access_token'], 'refresh_token': payload['refresh_token'],
                'scopes': str(payload['scope'] if 'scope' in payload else pending['scope']).split(),
                'expires_at': self.now() + float(payload.get('expires_in', 0))}
            value['active_profile_id'] = profile_id
            value.pop('pending_client_id', None)
            self._save(value)

    def _verify_identity(self, token: str, client_id: str, nonce: str) -> dict:
        keys = self.client.get(JWKS_URL)
        keys.raise_for_status()
        header = jwt.get_unverified_header(token)
        if header.get('alg') not in {'RS256', 'ES256'}:
            raise ValueError('Unexpected signing algorithm')
        key = next(row for row in keys.json()['keys'] if row.get('kid') == header.get('kid'))
        claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=[header['alg']],
                            audience=client_id, issuer=ISSUER,
                            options={'require': ['iss', 'aud', 'sub', 'exp', 'iat', 'nonce']})
        if not secrets.compare_digest(str(claims['nonce']), nonce):
            raise ValueError('Nonce mismatch')
        return claims

    def _post_token(self, form: dict) -> dict:
        try:
            response = self.client.post(TOKEN_URL, data=form)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except Exception as exc:
            # Provider bodies may echo secrets. Never expose them or exception URLs.
            raise ChatGPTPlanError('ChatGPT session exchange failed; reconnect the selected account.') from exc

    def access_token(self, expected_binding: str | None = None) -> str:
        self._require_local()
        with self._locked():
            value = self._load()
            profile = self._active(value)
            binding = hashlib.sha256(json.dumps([value['host_id'], profile.get('client_id'),
                                                 profile.get('subject')]).encode()).hexdigest()
            if expected_binding and binding != expected_binding:
                raise ChatGPTPlanError('ChatGPT account changed during the review; start a new review.')
            if not {PLAN_SCOPE, 'resource.invoke'}.issubset(set(profile.get('scopes', []))):
                raise ChatGPTPlanError('ChatGPT plan use is not authorized. Continue with ChatGPT in Console Settings.')
            if not profile.get('access_token') or not profile.get('refresh_token'):
                raise ChatGPTPlanError('Reconnect the selected ChatGPT account.')
            if float(profile.get('expires_at', 0)) - self.now() <= 60:
                payload = self._post_token({'grant_type': 'refresh_token', 'client_id': profile['client_id'],
                                           'refresh_token': profile['refresh_token'], 'resource': RESOURCE})
                if not payload.get('access_token') or float(payload.get('expires_in', 0)) <= 0:
                    raise ChatGPTPlanError('ChatGPT session could not be renewed; reconnect.')
                scopes = str(payload['scope']).split() if 'scope' in payload else profile['scopes']
                profile.update(access_token=payload['access_token'],
                               refresh_token=payload.get('refresh_token') or profile['refresh_token'],
                               scopes=scopes, expires_at=self.now() + float(payload['expires_in']))
                self._save(value)
                # Even a reduced grant rotates the renewable session. Retain the
                # replacement so readiness reflects revocation and sign-out can
                # revoke the current token rather than a spent predecessor.
                if not {PLAN_SCOPE, 'resource.invoke'}.issubset(set(scopes)):
                    raise ChatGPTPlanError('ChatGPT plan permission was revoked; reconnect.')
            return profile['access_token']

    def list_models(self) -> list[dict]:
        token = self.access_token()
        try:
            response = self.client.get(f'{RESOURCE}/models', headers={'Authorization': f'Bearer {token}'})
            response.raise_for_status()
            rows = response.json()['models']
            return [{'id': validate_model(row['slug']), 'display_name': str(row.get('display_name') or row['slug'])}
                    for row in rows if isinstance(row, dict) and row.get('visibility') == 'list']
        except Exception as exc:
            raise ChatGPTPlanError('Could not load the selected account model catalog. No fallback models were selected.') from exc

    def select_profile(self, profile_id: str) -> dict:
        self._require_local()
        with self._mutex, self._locked():
            value = self._load()
            if profile_id not in value['profiles'] or not value['profiles'][profile_id].get('subject'):
                raise ChatGPTPlanError('ChatGPT account registration not found.')
            self._pending = None
            self._generation += 1
            value['active_profile_id'] = profile_id
            self._save(value)
        self._stop_listener()
        return self.status()

    def select_judge_model(self, model: str | None) -> dict:
        self._require_local()
        if model is not None:
            model = validate_model(model)
            binding = self.binding()
            if model not in {row['id'] for row in self.list_models()}:
                raise ChatGPTPlanError('That model is not listed for the selected ChatGPT account.')
        with self._locked():
            value = self._load()
            profile = self._active(value)
            if not profile:
                raise ChatGPTPlanError('Connect ChatGPT before selecting its judge model.')
            current = hashlib.sha256(json.dumps([value['host_id'], profile.get('client_id'),
                                                 profile.get('subject')]).encode()).hexdigest()
            if model is not None and current != binding:
                raise ChatGPTPlanError('ChatGPT account changed; reload the model list.')
            profile['judge_model'] = MODEL_PREFIX + model if model else None
            value['judge_mode'] = 'chatgpt_plan' if model else 'deployment'
            self._save(value)
        return self.status()

    def disconnect(self) -> dict:
        self._require_local()
        confirmed = False
        with self._mutex:
            self._pending = None
            self._generation += 1
        self._stop_listener()
        with self._locked():
            value = self._load()
            profile = self._active(value)
            if profile.get('refresh_token'):
                try:
                    discovery = self.client.get(DISCOVERY_URL)
                    discovery.raise_for_status()
                    endpoint = discovery.json()['revocation_endpoint']
                    parsed = urlsplit(endpoint)
                    if parsed.scheme != 'https' or parsed.netloc != 'auth.openai.com':
                        raise ValueError
                    response = self.client.post(endpoint, data={'token': profile['refresh_token'],
                        'token_type_hint': 'refresh_token', 'client_id': profile['client_id']})
                    confirmed = response.status_code == 200
                except Exception:
                    pass
            for key in ('access_token', 'refresh_token', 'id_token', 'expires_at', 'scopes'):
                profile.pop(key, None)
            self._save(value)
        self._last_error = ('Signed out. Remote revocation was not confirmed; disconnect this app in ChatGPT Settings.'
                            if not confirmed else 'Signed out of ChatGPT.')
        return self.status()

    def _require_local(self) -> None:
        if not local_enabled():
            raise ChatGPTPlanError('ChatGPT plan use is available only in explicitly enabled local development mode.')

    def _start_listener(self) -> None:
        if self._timer:
            self._timer.cancel()
        if not self._server:
            provider = self

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self) -> None:  # noqa: N802
                    parsed = urlsplit(self.path)
                    if parsed.path != '/auth/callback':
                        self.send_error(404)
                        return
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if any(len(items) != 1 for items in query.values()):
                        self.send_error(400)
                        return
                    try:
                        provider.complete_callback(query)
                        text, code = 'ChatGPT connection saved. Return to ConVoice QA Console Settings.', 200
                    except Exception as exc:
                        text, code = str(exc) if isinstance(exc, ChatGPTPlanError) else 'ChatGPT sign-in failed.', 400
                        provider._last_error = text
                    body = text.encode()
                    self.send_response(code)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(body)
                    if provider._pending is None:
                        provider._stop_listener()

                def log_message(self, *args: Any) -> None:
                    pass  # Callback URLs contain authorization codes.

            self._server = ThreadingHTTPServer((os.getenv('CHATGPT_PLAN_CALLBACK_BIND_HOST', '127.0.0.1'), CALLBACK_PORT), Handler)
            # Callback handlers stop the listener after consuming the attempt.
            # Do not join the current request thread from server_close().
            self._server.daemon_threads = True
            threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self._timer = threading.Timer(600, self._expire, args=(self._generation,))
        self._timer.daemon = True
        self._timer.start()

    def _expire(self, generation: int) -> None:
        with self._mutex:
            if generation != self._generation:
                return
            self._pending = None
            self._generation += 1
        self._stop_listener()

    def _stop_listener(self) -> None:
        if self._timer:
            self._timer.cancel()
        server, self._server = self._server, None
        if server:
            server.shutdown()
            server.server_close()


_provider: ChatGPTPlanProvider | None = None
_provider_lock = threading.Lock()


def get_chatgpt_provider() -> ChatGPTPlanProvider:
    global _provider
    with _provider_lock:
        if _provider is None or _provider.path != store_path():
            _provider = ChatGPTPlanProvider()
        return _provider
