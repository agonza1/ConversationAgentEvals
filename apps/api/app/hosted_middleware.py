"""One hosted boundary for every route, including downloads and background execution."""
import json
import os
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, urlencode
from urllib.parse import urlsplit
from starlette.responses import JSONResponse
from starlette.routing import Match
from starlette.concurrency import run_in_threadpool
from app.security import FIREBASE_CONFIG_VARS, hosted, identity, request_maps, request_db
from app.services import firebase_auth
from app.services.rate_limit import admit

PUBLIC = {'/health', '/api/auth/config', '/api/auth/session'}
ACTORS = {'user_id', 'owner_user_id', 'requester_user_id', 'actor_user_id'}


def validate_hosted_configuration():
    mode = os.getenv('CAE_AUTH_MODE', '')
    if mode not in {'', 'local', 'firebase'}:
        raise RuntimeError('CAE_AUTH_MODE must be local or firebase.')
    if not hosted(): return
    required = [*FIREBASE_CONFIG_VARS, 'CAE_PUBLIC_ORIGIN', 'CAE_CREDENTIAL_ENCRYPTION_KEY', 'DATABASE_URL']
    if any(not os.getenv(name) for name in required):
        raise RuntimeError('Firebase hosted configuration is incomplete: ' + ', '.join(required))
    if os.getenv('APP_ENV') != 'development' and not os.environ['CAE_PUBLIC_ORIGIN'].startswith('https://'):
        raise RuntimeError('Hosted login requires an HTTPS public origin.')
    origin = urlsplit(os.environ['CAE_PUBLIC_ORIGIN'])
    if origin.scheme not in {'http', 'https'} or not origin.hostname or origin.username or origin.password or origin.path not in {'', '/'} or origin.query or origin.fragment:
        raise RuntimeError('CAE_PUBLIC_ORIGIN must be a web origin without a path, credentials, or query.')
    if origin.scheme == 'http' and origin.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise RuntimeError('HTTP hosted-mode development is restricted to loopback origins.')
    for name in ('CAE_REQUEST_RATE_PER_MINUTE', 'CAE_WRITE_RATE_PER_MINUTE', 'CAE_COMPUTE_RATE_PER_MINUTE',
                 'CAE_COMPUTE_RATE_PER_DAY', 'CAE_LOGIN_RATE_PER_MINUTE', 'CAE_MAX_REQUEST_BYTES'):
        if name in os.environ and (not os.environ[name].isdigit() or int(os.environ[name]) <= 0):
            raise RuntimeError(f'{name} must be a positive integer.')
    if os.getenv('FIREBASE_AUTH_EMULATOR_HOST'):
        raise RuntimeError('Firebase emulator credentials are not accepted in hosted mode.')
    if os.getenv('APP_ENV') != 'development' and not os.environ['DATABASE_URL'].startswith('postgresql'):
        raise RuntimeError('Hosted staging requires durable PostgreSQL storage.')
    forbidden = [name for name, value in os.environ.items() if value and (
        name.endswith(('_API_KEY', '_AUTH_TOKEN')) and not name.startswith('FIREBASE_')
        or name in {'OPENAI_API_KEY', 'LLM_JUDGE_API_KEY', 'AZURE_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS_JSON'}
    )]
    if forbidden:
        raise RuntimeError('Remove service provider credentials in BYOK mode: ' + ', '.join(sorted(forbidden)))
    from app.services.user_credentials import cipher
    cipher()


class HostedBoundary:
    def __init__(self, app, router):
        self.app, self.router = app, router

    async def __call__(self, scope, receive, send):
        if not hosted() or scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        path, method = scope['path'].rstrip('/') or '/', scope['method']
        headers = {key.decode().lower(): value.decode() for key, value in scope.get('headers', [])}

        async def reject(status, detail, retry_after=60):
            response = JSONResponse({'detail': detail}, status_code=status,
                                    headers={'Cache-Control': 'no-store', **({'Retry-After': str(retry_after)} if status == 429 else {})})
            return await response(scope, receive, send)

        if method not in {'GET', 'HEAD', 'OPTIONS'}:
            if headers.get('origin') != os.environ['CAE_PUBLIC_ORIGIN'].rstrip('/'):
                return await reject(403, 'Request origin is not allowed.')
        if path == '/api/auth/session':
            peer = str(scope.get('client', ('unknown',))[0])
            if not await run_in_threadpool(admit, peer, 'login', int(os.getenv('CAE_LOGIN_RATE_PER_MINUTE', '20'))):
                return await reject(429, 'Too many sign-in attempts. Retry shortly.')
            if method == 'POST':
                # Bound the unauthenticated token exchange before framework JSON parsing.
                login_body = b''
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect': return
                    login_body += message.get('body', b'')
                    if len(login_body) > 32768:
                        return await reject(413, 'Sign-in request is too large.')
                    if not message.get('more_body', False): break
                login_sent = False
                async def replay_login():
                    nonlocal login_sent
                    if login_sent: return await receive()
                    login_sent = True
                    return {'type': 'http.request', 'body': login_body, 'more_body': False}
                return await self.app(scope, replay_login, send)
        if path in PUBLIC:
            return await self.app(scope, receive, send)
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get('cookie', ''))
            claims = await run_in_threadpool(firebase_auth.verify_session, cookie['__session'].value)
            uid = claims['sub']
            if not isinstance(uid, str) or not 1 <= len(uid) <= 128:
                raise ValueError('Invalid subject')
            if claims.get('firebase', {}).get('sign_in_provider') != 'google.com' or not claims.get('email_verified'):
                raise ValueError('Google identity required')
        except Exception:
            return await reject(401, 'Sign in with Google to continue.')
        uid_token = identity.set(uid)
        maps_token = request_maps.set({})
        from app.db.database import SessionLocal
        db = SessionLocal()
        db_token = request_db.set(db)
        scope['cae_claims'] = claims
        try:
            if not await run_in_threadpool(admit, uid, 'requests', int(os.getenv('CAE_REQUEST_RATE_PER_MINUTE', '180'))):
                return await reject(429, 'Request limit reached. Retry shortly.')
            if method not in {'GET', 'HEAD', 'OPTIONS'} and not await run_in_threadpool(admit, uid, 'writes', int(os.getenv('CAE_WRITE_RATE_PER_MINUTE', '30'))):
                return await reject(429, 'Action limit reached. Retry shortly.')
            if path.startswith(('/storage', '/pipecat', '/api/decks', '/api/sessions', '/api/realtime', '/api/bootstrap', '/api/product/providers/chatgpt', '/api/waylo')) or '/oauth/' in path:
                return await reject(403, 'This local-only feature is unavailable in hosted BYOK mode.')
            if path.startswith('/api/execution/') and not (path.startswith('/api/execution/runs') or path in {'/api/execution/health', '/api/execution/audio/capabilities', '/api/execution/speech-settings'}):
                return await reject(403, 'This voice or local-service feature is unavailable in hosted BYOK mode.')
            # Bind legacy actor parameters at the server boundary; uploaded evidence is never rewritten.
            matched = next((route for route in self.router.routes if route.matches(scope)[0] == Match.FULL), None)
            dependant = getattr(matched, 'dependant', None)
            query = dict(parse_qsl(scope.get('query_string', b'').decode(), keep_blank_values=True))
            if dependant:
                for field in dependant.query_params:
                    if field.name in ACTORS: query[field.alias] = uid
            scope['query_string'] = urlencode(query).encode()
            body = b''
            if method in {'POST', 'PUT', 'PATCH'}:
                more = True
                while more:
                    message = await receive()
                    if message['type'] == 'http.disconnect': return
                    body += message.get('body', b'')
                    if len(body) > int(os.getenv('CAE_MAX_REQUEST_BYTES', '5242880')):
                        return await reject(413, 'Request is too large.')
                    more = message.get('more_body', False)
                media_type = headers.get('content-type', '').split(';', 1)[0].strip().lower()
                is_json = not media_type or media_type == 'application/json' or (media_type.startswith('application/') and media_type.endswith('+json'))
                if body and is_json:
                    try: payload = json.loads(body)
                    except ValueError: return await reject(400, 'Invalid JSON request.')
                    if isinstance(payload, dict) and dependant:
                        fields = getattr(getattr(matched, 'body_field', None), 'type_', None)
                        names = getattr(fields, 'model_fields', {})
                        for name in ACTORS & names.keys():
                            if name == 'user_id' and path.endswith('/members'): continue
                            payload[name] = uid
                        if 'plan' in names: payload['plan'] = 'free'
                        if path == '/api/execution/runs':
                            if payload.get('mode', 'text_callable') != 'text_callable' or payload.get('text_callable') not in (None, 'mock_agent', 'openai_codex'):
                                return await reject(403, 'Hosted staging currently supports sample text and your OpenAI API key. Voice and external executors are disabled.')
                            if payload.get('voice_fixture_path') or payload.get('audio_plan_path'):
                                return await reject(403, 'Server file inputs are unavailable in hosted mode.')
                            if (isinstance(payload.get('iterations'), int) and payload['iterations'] > 3
                                or isinstance(payload.get('max_exchanges'), int) and payload['max_exchanges'] > 3
                                or isinstance(payload.get('scenario_ids'), list) and len(payload['scenario_ids']) > 10):
                                return await reject(422, 'Use at most 3 iterations and 10 scenarios per run.')
                        if path.startswith('/api/agents') and method in {'POST', 'PATCH'}:
                            if payload.get('target') not in (None, 'mock_agent', 'openai_codex'):
                                return await reject(403, 'Only sample and OpenAI text agents are enabled in hosted staging.')
                            if isinstance(payload.get('connection'), dict) and payload['connection'].get('secret_ref'):
                                return await reject(403, 'Deployment credentials are unavailable in hosted mode.')
                        body = json.dumps(payload).encode()
                sensitive = any(part in path for part in ('/judge', '/generate', '/assisted')) or path == '/api/execution/runs'
                if sensitive:
                    if not await run_in_threadpool(admit, uid, 'compute', int(os.getenv('CAE_COMPUTE_RATE_PER_MINUTE', '5'))):
                        return await reject(429, 'Evaluation/generation limit reached. Retry shortly.')
                    if not await run_in_threadpool(admit, uid, 'compute-daily', int(os.getenv('CAE_COMPUTE_RATE_PER_DAY', '100')), 86400):
                        return await reject(429, 'Daily evaluation/generation limit reached.', 86400 - int(time.time()) % 86400)
                scope['headers'] = [(key, value) for key, value in scope['headers'] if key.lower() != b'content-length'] + [(b'content-length', str(len(body)).encode())]
                sent = False

                async def replay():
                    nonlocal sent
                    if sent: return await receive()
                    sent = True
                    return {'type': 'http.request', 'body': body, 'more_body': False}
                request_receive = replay
            else:
                request_receive = receive
            async def private_send(message):
                if message['type'] == 'http.response.start':
                    if message['status'] < 400: db.commit()
                    else: db.rollback()
                    message['headers'] = [(key, value) for key, value in message['headers'] if key.lower() != b'cache-control'] + [(b'cache-control', b'no-store')]
                await send(message)
            await self.app(scope, request_receive, private_send)
            db.commit()
        finally:
            db.close()
            request_db.reset(db_token)
            request_maps.reset(maps_token)
            identity.reset(uid_token)
