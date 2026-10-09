import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.services.llm_providers import chatgpt_plan as cp
from app.integrations import chatgpt_assert_cli as transport


@pytest.fixture
def provider(monkeypatch, tmp_path):
    monkeypatch.setenv('CHATGPT_PLAN_LOCAL_ENABLED', '1')
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('OPENAI_CODEX_OAUTH_PATH', str(tmp_path / 'legacy.json'))
    instance = cp.ChatGPTPlanProvider(path=cp.store_path(), now=lambda: 1000,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        verify_identity=lambda token, client, nonce: {'sub': 'user-1', 'email': 'test@example.test'})
    monkeypatch.setattr(instance, '_start_listener', lambda: None)
    monkeypatch.setattr(cp, '_provider', instance)
    return instance


def authorize(provider, monkeypatch, *, scopes=cp.SCOPES, profile_id=None, client_id='oaiapp_test'):
    calls = []
    monkeypatch.setattr(provider, '_post_token', lambda form: calls.append(form) or {
        'id_token': 'id-secret', 'access_token': 'access-secret', 'refresh_token': 'refresh-secret',
        'expires_in': 3600, 'scope': scopes})
    start = provider.start_oauth(profile_id)
    params = parse_qs(urlsplit(start['authorize_url']).query)
    provider.complete_callback({'state': params['state'], 'code': ['code-secret'], 'client_id': [client_id]})
    return params, calls


def test_dynamic_registration_has_required_scopes_host_pkce_and_nonce(provider, monkeypatch):
    params, calls = authorize(provider, monkeypatch)
    assert params['client_id'] == ['dynamic_agent_client']
    assert params['scope'] == [cp.SCOPES]
    assert params['resource'] == [cp.RESOURCE]
    assert params['redirect_uri'] == ['http://127.0.0.1:1456/auth/callback']
    assert params['code_challenge_method'] == ['S256']
    assert params['nonce'] and params['code_challenge']
    assert params['ext_agent_host_id'][0].startswith('urn:uuid:')
    assert calls[0]['client_id'] == 'oaiapp_test'
    assert calls[0]['redirect_uri'] == params['redirect_uri'][0]
    assert provider.status()['sharing'] is True
    assert 'access-secret' not in json.dumps(provider.status())
    assert 'refresh-secret' not in json.dumps(provider.status())
    assert 'id-secret' not in json.dumps(provider.status())


def test_omitted_scope_retains_the_requested_authorization_grant(provider, monkeypatch):
    monkeypatch.setattr(provider, '_post_token', lambda form: {
        'id_token': 'id-secret', 'access_token': 'access-secret',
        'refresh_token': 'refresh-secret', 'expires_in': 3600})
    start = provider.start_oauth()
    params = parse_qs(urlsplit(start['authorize_url']).query)
    provider.complete_callback({'state': params['state'], 'code': ['x'], 'client_id': ['oaiapp_test']})
    assert provider.status()['sharing'] is True
    assert provider.access_token() == 'access-secret'
    assert set(provider._active(provider._load())['scopes']) == set(cp.SCOPES.split())


@pytest.mark.parametrize('scopes', ['', 'openid profile email offline_access'])
def test_explicit_reduced_scope_is_not_replaced_with_requested_plan_grant(provider, monkeypatch, scopes):
    authorize(provider, monkeypatch, scopes=scopes)
    assert provider.status()['sharing'] is False
    with pytest.raises(cp.ChatGPTPlanError, match='not authorized'):
        provider.access_token()


@pytest.mark.parametrize('disabled_by', ['feature', 'production'])
def test_disabling_local_plan_preserves_billing_choice_and_blocks_paid_fallback(provider, monkeypatch, disabled_by):
    from app.services import upstream_assert_judge as judge
    from test_upstream_assert_judge import _run_and_conversation, _scenario_contract
    authorize(provider, monkeypatch)
    monkeypatch.setattr(provider, 'list_models', lambda: [{'id': 'gpt-test'}])
    provider.select_judge_model('gpt-test')
    if disabled_by == 'feature': monkeypatch.setenv('CHATGPT_PLAN_LOCAL_ENABLED', '0')
    else: monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'paid-key-must-not-be-used')
    monkeypatch.setenv('ASSERT_JUDGE_MODEL', 'openai/gpt-4.1-mini')
    assert provider.status()['enabled'] is False
    assert provider.status()['judge_model'] == 'chatgpt_plan/gpt-test'
    assert judge._resolve_model(None) == 'chatgpt_plan/gpt-test'
    readiness = judge.assert_judge_readiness()
    assert readiness['ready'] is False
    assert readiness['model'] == 'chatgpt_plan/gpt-test'
    run, conversation = _run_and_conversation()
    with pytest.raises(judge.UpstreamAssertJudgeUnavailable, match='Enable CHATGPT_PLAN_LOCAL_ENABLED'):
        judge.run_upstream_assert_judge(run=run, conversation=conversation, scenario_contract=_scenario_contract())


def test_disabled_status_without_saved_choice_creates_no_credential_storage(provider, monkeypatch):
    monkeypatch.setenv('CHATGPT_PLAN_LOCAL_ENABLED', '0')
    assert provider.status()['judge_model'] is None
    assert not provider.path.exists()
    assert not provider.path.with_name(provider.path.name + '.lock').exists()


def test_reauthorization_retains_client_host_and_active_account_on_failure(provider, monkeypatch):
    params, _ = authorize(provider, monkeypatch)
    active = provider.status()['active_profile_id']
    start = provider.start_oauth(active)
    again = parse_qs(urlsplit(start['authorize_url']).query)
    assert again['client_id'] == ['oaiapp_test']
    assert again['ext_agent_host_id'] == params['ext_agent_host_id']
    assert 'agent_name_hint' not in again
    assert again['id_token_hint'] == ['id-secret']
    with pytest.raises(cp.ChatGPTPlanError, match='changed the selected'):
        provider.complete_callback({'state': again['state'], 'code': ['x'], 'client_id': ['oaiapp_other']})
    assert provider.status()['active_profile_id'] == active
    assert provider.access_token() == 'access-secret'


@pytest.mark.parametrize('failure', ['exchange', 'identity', 'missing-code'])
@pytest.mark.parametrize('existing_account', [False, True])
def test_failed_dynamic_registration_can_be_abandoned_without_replacing_account(provider, monkeypatch, failure, existing_account):
    if existing_account:
        authorize(provider, monkeypatch)
    before = provider._load()
    attempt = parse_qs(urlsplit(provider.start_oauth()['authorize_url']).query)
    def reject(*args):
        raise cp.ChatGPTPlanError('Synthetic failure.')
    if failure == 'exchange':
        monkeypatch.setattr(provider, '_post_token', reject)
    elif failure == 'identity':
        monkeypatch.setattr(provider, '_post_token', lambda *_: {'id_token': 'bad'})
        monkeypatch.setattr(provider, 'verify_identity', reject)
    query = {'state': attempt['state'], 'client_id': ['oaiapp_failed_registration']}
    if failure != 'missing-code':
        query['code'] = ['synthetic']
    with pytest.raises(cp.ChatGPTPlanError):
        provider.complete_callback(query)
    stored = provider._load()
    assert stored['profiles'] == before['profiles']
    assert stored['active_profile_id'] == before['active_profile_id']
    assert 'pending_client_id' not in stored
    new_attempt = parse_qs(urlsplit(provider.start_oauth()['authorize_url']).query)
    assert new_attempt['client_id'] == ['dynamic_agent_client']
    assert new_attempt['state'] != attempt['state']
    assert new_attempt['ext_agent_host_id'] == attempt['ext_agent_host_id']
    if existing_account:
        reconnect = parse_qs(urlsplit(provider.start_oauth(before['active_profile_id'])['authorize_url']).query)
        assert reconnect['client_id'] == ['oaiapp_test']


def test_new_registration_discards_previously_saved_unvalidated_client(provider):
    value = provider._load()
    value['pending_client_id'] = 'oaiapp_unvalidated_old_attempt'
    provider._save(value)
    attempt = parse_qs(urlsplit(provider.start_oauth()['authorize_url']).query)
    assert attempt['client_id'] == ['dynamic_agent_client']
    assert 'pending_client_id' not in provider._load()


@pytest.mark.parametrize('failure', ['state', 'expired', 'denied', 'missing-client', 'identity', 'subject'])
def test_callback_security_and_one_time_consumption(provider, monkeypatch, failure):
    authorize(provider, monkeypatch)
    active = provider.status()['active_profile_id']
    start = provider.start_oauth(active if failure == 'subject' else None)
    params = parse_qs(urlsplit(start['authorize_url']).query)
    query = {'state': params['state'], 'code': ['x'], 'client_id': ['oaiapp_test']}
    if failure == 'state': query['state'] = ['incorrect']
    if failure == 'expired': provider._pending['expires_at'] = 999
    if failure == 'denied': query['error'] = ['access_denied']
    if failure == 'missing-client': query.pop('client_id')
    if failure == 'identity': monkeypatch.setattr(provider, 'verify_identity', lambda *args: (_ for _ in ()).throw(ValueError('secret')))
    if failure == 'subject': monkeypatch.setattr(provider, 'verify_identity', lambda *args: {'sub': 'different-user'})
    with pytest.raises(cp.ChatGPTPlanError): provider.complete_callback(query)
    assert provider.status()['active_profile_id'] == active
    if failure not in {'state', 'expired'}:
        with pytest.raises(cp.ChatGPTPlanError): provider.complete_callback(query)


def test_identity_only_connection_cannot_infer_or_make_readiness_true(provider, monkeypatch):
    authorize(provider, monkeypatch, scopes='openid profile email offline_access')
    assert provider.status()['status'] == 'connected'
    assert provider.status()['sharing'] is False
    with pytest.raises(cp.ChatGPTPlanError, match='not authorized'): provider.access_token()


def test_corrupt_saved_billing_mode_blocks_readiness_without_paid_fallback(provider, monkeypatch):
    from app.services import upstream_assert_judge as judge
    authorize(provider, monkeypatch)
    provider.path.write_text('{invalid', encoding='utf-8')
    monkeypatch.setenv('OPENAI_API_KEY', 'paid-key-must-not-be-used')
    monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')
    ready = judge.assert_judge_readiness()
    assert ready['ready'] is False
    assert ready['model'] == 'chatgpt_plan/repair-storage'
    assert 'storage is unavailable' in ready['message']


def test_cancelled_callback_cannot_resurrect_a_disconnected_session(provider, monkeypatch):
    authorize(provider, monkeypatch)
    active = provider.status()['active_profile_id']
    start = provider.start_oauth(active)
    params = parse_qs(urlsplit(start['authorize_url']).query)
    def identity(*args):
        provider.disconnect()
        return {'sub': 'user-1'}
    monkeypatch.setattr(provider, 'verify_identity', identity)
    with pytest.raises(cp.ChatGPTPlanError, match='cancelled'):
        provider.complete_callback({'state': params['state'], 'code': ['x'], 'client_id': ['oaiapp_test']})
    assert provider.status()['status'] == 'disconnected'


def test_readiness_never_refreshes_tokens_or_calls_inference(provider, monkeypatch):
    from app.services import upstream_assert_judge as judge
    authorize(provider, monkeypatch)
    monkeypatch.setattr(provider, 'list_models', lambda: [{'id': 'gpt-test'}])
    provider.select_judge_model('gpt-test')
    monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')
    monkeypatch.setattr(provider, '_post_token', lambda form: pytest.fail('Readiness refreshed credentials'))
    monkeypatch.setattr(provider, 'access_token', lambda *args: pytest.fail('Readiness accessed live credentials'))
    assert judge.assert_judge_readiness()['model'] == 'chatgpt_plan/gpt-test'


def test_account_selection_cancels_pending_callback(provider, monkeypatch):
    authorize(provider, monkeypatch)
    active = provider.status()['active_profile_id']
    start = provider.start_oauth(active)
    query = {'state': parse_qs(urlsplit(start['authorize_url']).query)['state'],
             'code': ['x'], 'client_id': ['oaiapp_test']}
    def identity(*args):
        provider.select_profile(active)
        return {'sub': 'user-1'}
    monkeypatch.setattr(provider, 'verify_identity', identity)
    with pytest.raises(cp.ChatGPTPlanError, match='cancelled'):
        provider.complete_callback(query)
    assert provider.status()['active_profile_id'] == active


def test_signed_out_account_can_explicitly_restore_deployment_judge(provider, monkeypatch):
    authorize(provider, monkeypatch)
    monkeypatch.setattr(provider, 'list_models', lambda: [{'id': 'gpt-test'}])
    provider.select_judge_model('gpt-test')
    provider.disconnect()
    assert provider.selected_model() == 'chatgpt_plan/gpt-test'
    provider.select_judge_model(None)
    assert provider.selected_model() is None


def test_old_expiry_timer_cannot_cancel_a_new_authorization(provider):
    provider.start_oauth()
    older = provider._generation
    provider.start_oauth()
    provider._expire(older)
    assert provider.status()['pending'] is True


def test_invalid_profile_does_not_cancel_an_existing_authorization(provider):
    provider.start_oauth()
    generation = provider._generation
    pending = provider._pending.copy()
    with pytest.raises(cp.ChatGPTPlanError, match='existing ChatGPT account'):
        provider.start_oauth('missing-profile')
    assert provider._generation == generation
    assert provider._pending == pending
    provider._expire(generation)
    assert provider.status()['pending'] is False


@pytest.mark.parametrize('cancellation', ['select_profile', 'disconnect', 'expire'])
def test_cancellation_shutdown_cannot_close_a_newer_oauth_listener(provider, monkeypatch, cancellation):
    import threading
    authorize(provider, monkeypatch)
    profile_id = provider.status()['active_profile_id']
    provider.start_oauth()
    generation = provider._generation
    shutdown_started = threading.Event()
    allow_shutdown = threading.Event()
    new_attempt_started = threading.Event()
    new_attempt_done = threading.Event()
    errors = []
    class Timer:
        cancelled = False
        def cancel(self):
            self.cancelled = True
    class Server:
        closed = False
        def shutdown(self):
            shutdown_started.set()
            assert allow_shutdown.wait(5)
        def server_close(self):
            self.closed = True
    old_server, old_timer = Server(), Timer()
    new_server, new_timer = Server(), Timer()
    provider._server, provider._timer = old_server, old_timer
    def start_listener():
        assert old_server.closed  # New bind must wait for the old socket close.
        provider._server, provider._timer = new_server, new_timer
    monkeypatch.setattr(provider, '_start_listener', start_listener)
    def cancel():
        try:
            if cancellation == 'select_profile': provider.select_profile(profile_id)
            elif cancellation == 'disconnect': provider.disconnect()
            else: provider._expire(generation)
        except Exception as exc:
            errors.append(exc)
    def start():
        new_attempt_started.set()
        try:
            provider.start_oauth()
        except Exception as exc:
            errors.append(exc)
        finally:
            new_attempt_done.set()
    cancel_thread = threading.Thread(target=cancel)
    start_thread = threading.Thread(target=start)
    cancel_thread.start()
    try:
        assert shutdown_started.wait(5)
        start_thread.start()
        assert new_attempt_started.wait(5)
        assert not new_attempt_done.wait(0.05)
    finally:
        allow_shutdown.set()
        cancel_thread.join(5)
        if start_thread.ident is not None:
            start_thread.join(5)
    assert not cancel_thread.is_alive() and not start_thread.is_alive()
    assert not errors
    assert provider._pending is not None and provider._pending['generation'] > generation
    assert provider._server is new_server and not new_server.closed
    assert old_timer.cancelled and not new_timer.cancelled
    provider._stop_listener()


def test_refresh_retains_registration_grant_and_rotates_tokens(provider, monkeypatch):
    authorize(provider, monkeypatch)
    with provider._locked():
        value = provider._load()
        provider._active(value)['expires_at'] = 1001
        provider._save(value)
    calls = []
    monkeypatch.setattr(provider, '_post_token', lambda form: calls.append(form) or {
        'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3600})
    binding = provider.binding()
    assert provider.access_token(binding) == 'new-access'
    assert calls == [{'grant_type': 'refresh_token', 'client_id': 'oaiapp_test',
                      'refresh_token': 'refresh-secret', 'resource': cp.RESOURCE}]
    assert provider.binding() == binding
    assert provider.status()['sharing']


@pytest.mark.parametrize('code', ['invalid_grant', 'invalid_refresh_token', 'token_expired',
    'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused'])
@pytest.mark.parametrize('structured', [False, True])
def test_terminal_refresh_clears_only_unusable_tokens_and_blocks_readiness(provider, monkeypatch, code, structured):
    from app.services import upstream_assert_judge as judge
    authorize(provider, monkeypatch)
    monkeypatch.setattr(provider, 'list_models', lambda: [{'id': 'gpt-test'}])
    provider.select_judge_model('gpt-test')
    value = provider._load()
    active = value['active_profile_id']
    value['profiles'][active]['expires_at'] = 999
    provider._save(value)
    binding = provider.binding()
    # Exercise the real error classification rather than a mocked token helper.
    monkeypatch.setattr(provider, '_post_token', cp.ChatGPTPlanProvider._post_token.__get__(provider))
    provider.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(400,
        json={'error': {'code': code, 'message': 'secret diagnostic'} if structured else code})))
    with pytest.raises(cp.ChatGPTPlanError, match='no longer valid') as error:
        provider.access_token(binding)
    assert 'secret' not in str(error.value)
    stored = provider._load()['profiles'][active]
    assert not set(stored).intersection({'access_token', 'refresh_token', 'id_token', 'expires_at', 'scopes'})
    assert stored['client_id'] == 'oaiapp_test' and stored['subject'] == 'user-1'
    assert provider.binding() == binding
    assert provider.status()['sharing'] is False
    assert provider.status()['status'] == 'disconnected'
    monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')
    ready = judge.assert_judge_readiness()
    assert ready['ready'] is False and ready['model'] == 'chatgpt_plan/gpt-test'
    params = parse_qs(urlsplit(provider.start_oauth(active)['authorize_url']).query)
    assert params['client_id'] == ['oaiapp_test']


@pytest.mark.parametrize('failure', ['network', 'server', 'rate-limit', 'invalid-client', 'unknown', 'malformed'])
def test_transient_or_unconfirmed_refresh_failure_preserves_credentials(provider, monkeypatch, failure):
    authorize(provider, monkeypatch)
    value = provider._load()
    value['profiles'][value['active_profile_id']]['expires_at'] = 999
    provider._save(value)
    monkeypatch.setattr(provider, '_post_token', cp.ChatGPTPlanProvider._post_token.__get__(provider))
    def handler(request):
        if failure == 'network':
            raise httpx.ConnectError('temporary network', request=request)
        if failure == 'malformed':
            return httpx.Response(400, text='not JSON')
        status = 503 if failure == 'server' else 429 if failure == 'rate-limit' else 400
        code = 'invalid_client' if failure == 'invalid-client' else 'unknown' if failure == 'unknown' else 'invalid_grant'
        return httpx.Response(status, json={'error': code})
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(cp.ChatGPTPlanError):
        provider.access_token()
    assert provider._load() == value


@pytest.mark.parametrize('field,bad', [
    ('expires_at', 'bad'), ('expires_at', None), ('expires_at', True),
    ('expires_at', float('nan')), ('expires_at', float('inf')), ('expires_at', 10 ** 400),
    ('scopes', None), ('scopes', 'openid'), ('scopes', {}), ('scopes', [None]),
    ('access_token', 42), ('refresh_token', []), ('id_token', {}), ('email', []),
    ('client_id', None), ('subject', False), ('judge_model', 'openai/paid-model'),
])
def test_malformed_stored_profile_enters_safe_storage_repair(provider, monkeypatch, field, bad):
    from app.services import upstream_assert_judge as judge
    from app.main import app
    authorize(provider, monkeypatch)
    value = provider._load()
    value['profiles'][value['active_profile_id']][field] = bad
    provider._save(value)
    with pytest.raises(cp.ChatGPTPlanError, match='storage is unavailable'):
        provider.status()
    with TestClient(app, base_url='http://127.0.0.1') as client:
        response = client.get('/api/product/providers/chatgpt/status')
    assert response.status_code == 409
    assert 'secret' not in response.text and 'bad' not in response.text
    monkeypatch.setenv('OPENAI_API_KEY', 'paid-key-must-not-be-used')
    monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')
    ready = judge.assert_judge_readiness()
    assert ready['ready'] is False and ready['model'] == 'chatgpt_plan/repair-storage'


@pytest.mark.parametrize('field,bad', [('host_id', {}), ('active_profile_id', {}),
    ('active_profile_id', 'unknown'), ('judge_mode', 'unknown'), ('profiles', {'bad': None})])
def test_malformed_store_structure_is_reported_as_storage_repair(provider, field, bad):
    value = provider._load()
    value[field] = bad
    provider._save(value)
    with pytest.raises(cp.ChatGPTPlanError, match='storage is unavailable'):
        provider.status()


def test_reduced_refresh_grant_is_retained_and_latest_session_can_be_revoked(provider, monkeypatch):
    from app.services import upstream_assert_judge as judge
    authorize(provider, monkeypatch)
    monkeypatch.setattr(provider, 'list_models', lambda: [{'id': 'gpt-test'}])
    provider.select_judge_model('gpt-test')
    with provider._locked():
        value = provider._load()
        provider._active(value)['expires_at'] = 1001
        provider._save(value)
    monkeypatch.setattr(provider, '_post_token', lambda form: {
        'access_token': 'identity-access', 'refresh_token': 'rotated-refresh',
        'expires_in': 3600, 'scope': 'openid profile email offline_access'})
    with pytest.raises(cp.ChatGPTPlanError, match='permission was revoked'):
        provider.access_token()
    assert provider.status()['sharing'] is False
    monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')
    assert judge.assert_judge_readiness()['ready'] is False
    assert judge._resolve_model(None) == 'chatgpt_plan/gpt-test'
    revoked = []
    def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'revocation_endpoint': cp.ISSUER + '/revoke'})
        revoked.append(parse_qs(request.content.decode()))
        return httpx.Response(200)
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert provider.disconnect()['status'] == 'disconnected'
    assert revoked[0]['token'] == ['rotated-refresh']
    assert revoked[0]['client_id'] == ['oaiapp_test']


def test_account_switch_requires_new_model_no_paid_fallback(provider, monkeypatch):
    from app.services import upstream_assert_judge as judge
    authorize(provider, monkeypatch)
    first = provider.status()['active_profile_id']
    monkeypatch.setattr(provider, 'list_models', lambda: [{'id': 'gpt-test', 'display_name': 'Test'}])
    provider.select_judge_model('gpt-test')
    config1 = judge.judge_configuration('chatgpt_plan/gpt-test', 1)
    authorize(provider, monkeypatch, client_id='oaiapp_other')
    assert judge._resolve_model(None) == 'chatgpt_plan/select-model'
    with pytest.raises(judge.UpstreamAssertJudgeUnavailable, match='No API-key fallback'):
        judge._require_provider_credentials(judge._resolve_model(None), {})
    with pytest.raises(cp.ChatGPTPlanError, match='account changed'):
        provider.access_token(config1['transport']['account_binding_sha256'])
    provider.select_profile(first)
    assert judge._resolve_model(None) == 'chatgpt_plan/gpt-test'
    assert judge.judge_configuration('chatgpt_plan/gpt-test', 1) == config1
    assert config1['model_settings'] == {'name': 'chatgpt_plan/gpt-test'}
    assert 'secret' not in json.dumps(config1)


def test_account_supported_models_only_and_no_fallback(provider, monkeypatch):
    authorize(provider, monkeypatch)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'models': [
            {'slug': 'gpt-a', 'display_name': 'A', 'visibility': 'list'},
            {'slug': 'hidden', 'visibility': 'hidden'}, {'slug': 'gpt-b', 'visibility': 'list'}]})
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert provider.list_models() == [{'id': 'gpt-a', 'display_name': 'A'}, {'id': 'gpt-b', 'display_name': 'gpt-b'}]
    assert str(requests[0].url) == cp.RESOURCE + '/models'
    assert requests[0].headers['authorization'] == 'Bearer access-secret'
    with pytest.raises(cp.ChatGPTPlanError, match='not listed'): provider.select_judge_model('gpt-unknown')


@pytest.mark.parametrize('claim', ['aud', 'iss', 'exp', 'nonce', 'signature'])
def test_real_signed_id_token_validation(provider, claim):
    import time
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public['kid'] = 'test-key'
    claims = {'iss': cp.ISSUER, 'aud': 'oaiapp_test', 'sub': 'user-1',
              'iat': int(time.time()), 'exp': int(time.time()) + 600, 'nonce': 'nonce'}
    if claim in {'aud', 'iss', 'nonce'}: claims[claim] = 'bad'
    if claim == 'exp': claims['exp'] = int(time.time()) - 100
    signed_with = rsa.generate_private_key(public_exponent=65537, key_size=2048) if claim == 'signature' else key
    token = jwt.encode(claims, signed_with, algorithm='RS256', headers={'kid': 'test-key'})
    provider.client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'keys': [public]})))
    with pytest.raises((jwt.PyJWTError, ValueError)): provider._verify_identity(token, 'oaiapp_test', 'nonce')
    good = jwt.encode({**claims, 'iss': cp.ISSUER, 'aud': 'oaiapp_test', 'exp': int(time.time()) + 600, 'nonce': 'nonce'}, key,
                      algorithm='RS256', headers={'kid': 'test-key'})
    assert provider._verify_identity(good, 'oaiapp_test', 'nonce')['sub'] == 'user-1'


@pytest.mark.parametrize('terminal', ['response.failed', 'response.incomplete', 'error', 'truncated'])
def test_partial_stream_never_produces_review(terminal):
    events = [{'type': 'response.output_text.delta', 'delta': '{"looks":"valid"}'}]
    if terminal != 'truncated': events.append({'type': terminal, 'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}})
    with pytest.raises(cp.ChatGPTPlanError): transport.collect_response(['data: ' + json.dumps(e) for e in events])


@pytest.mark.parametrize('raw_timeout, expected_timeout', [
    (None, 300), ('invalid', 300), ('0', 300), ('-1', 300), ('1.5', 300),
    ('', 300), ('NaN', 300), ('inf', 300), ('45', 45),
])
def test_real_litellm_and_assert_structured_transport_use_responses(provider, monkeypatch, raw_timeout, expected_timeout):
    authorize(provider, monkeypatch)
    if raw_timeout is None:
        monkeypatch.delenv('ASSERT_JUDGE_TIMEOUT_SECONDS', raising=False)
    else:
        monkeypatch.setenv('ASSERT_JUDGE_TIMEOUT_SECONDS', raw_timeout)
    from app.services.upstream_assert_judge import judge_configuration
    assert judge_configuration('chatgpt_plan/gpt-test', 1)['timeout_seconds'] == expected_timeout
    from assert_ai.core.model_client import generate_structured, GenerateOptions
    payloads = []
    def handler(request):
        assert request.extensions['timeout']['read'] == expected_timeout
        payloads.append(json.loads(request.content))
        event = {'type': 'response.completed', 'response': {'id': 'resp-test', 'status': 'completed',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"ok":true}'}]}],
            'usage': {'input_tokens': 7, 'output_tokens': 4, 'total_tokens': 11}}}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\n', headers={'content-type': 'text/event-stream'})
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setenv('CAE_CHATGPT_EXPECTED_BINDING', provider.binding())
    transport.register_transport()
    result = asyncio.run(generate_structured('chatgpt_plan/gpt-test', [{'role': 'system', 'content': 'Judge'},
        {'role': 'user', 'content': 'Evidence'}], schema_name='judge', json_schema={'type': 'object',
        'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False},
        options=GenerateOptions(max_tokens=8000, temperature=0)))
    assert result.text == '{"ok":true}'
    assert result.usage.total_tokens == 11
    assert payloads[0]['model'] == 'gpt-test'
    assert payloads[0]['stream'] is True and payloads[0]['store'] is False
    assert payloads[0]['input'][0]['role'] == 'developer'
    assert payloads[0]['text']['format']['type'] == 'json_schema'
    assert 'max_output_tokens' not in payloads[0] and 'temperature' not in payloads[0]


def test_local_controls_reject_cross_site_and_hosted_mode(provider, monkeypatch):
    from app.main import app
    client = TestClient(app, base_url='http://127.0.0.1')
    endpoint = '/api/product/providers/chatgpt/'
    assert client.post(endpoint + 'account', json={'profile_id': 'anything'}).status_code == 403
    assert client.post(endpoint + 'oauth/start', json={}, headers={
        'x-cae-local-control': '1', 'origin': 'https://evil.example'}).status_code == 403
    assert client.get(endpoint + 'status', headers={'host': 'cae.example'}).status_code == 403
    monkeypatch.setenv('APP_ENV', 'production')
    assert client.get(endpoint + 'status').json()['enabled'] is False
    assert client.post(endpoint + 'oauth/start', json={}, headers={'x-cae-local-control': '1'}).status_code == 409


def test_disconnect_clears_tokens_retains_client_reports_unconfirmed_revocation(provider, monkeypatch):
    authorize(provider, monkeypatch)
    active = provider.status()['active_profile_id']
    status = provider.disconnect()
    assert status['status'] == 'disconnected' and status['sharing'] is False
    assert 'not confirmed' in status['message']
    stored = provider._load()['profiles'][active]
    assert stored['client_id'] == 'oaiapp_test'
    assert 'refresh_token' not in stored and 'access_token' not in stored and 'id_token' not in stored


def test_shared_judge_pipeline_selects_registered_cli_without_forwarding_tokens(provider, monkeypatch, tmp_path):
    from test_upstream_assert_judge import (_run_and_conversation, _scenario_contract,
        _configure_assert_runtime, _install_fake_assert, _valid_score)
    from app.services import upstream_assert_judge as judge
    authorize(provider, monkeypatch)
    monkeypatch.setattr(provider, 'list_models', lambda: [{'id': 'gpt-test'}])
    provider.select_judge_model('gpt-test')
    _configure_assert_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv('LLM_JUDGE_API_KEY')
    run, conversation = _run_and_conversation()
    def writer(path, config):
        model = config['pipeline']['judge']['model']
        assert model == {'name': 'chatgpt_plan/gpt-test'}
        path.write_text(json.dumps(_valid_score(run, conversation, model['name'])) + '\n')
    captured = _install_fake_assert(monkeypatch, writer)
    readiness = judge.assert_judge_readiness()
    assert readiness['ready'] and readiness['credentials_ready']
    claimed = judge.assert_judge_input_fingerprint(run=run, conversation=conversation,
        scenario_contract=_scenario_contract(), model='chatgpt_plan/gpt-test', judge_n=1)
    result = judge.run_upstream_assert_judge(run=run, conversation=conversation,
        scenario_contract=_scenario_contract(), artifact_root=tmp_path / 'artifacts',
        expected_input_fingerprint=claimed)
    assert result['status'] == 'ready'
    assert result['input_fingerprint'] == claimed
    assert result['model'] == 'chatgpt_plan/gpt-test'
    assert captured['command'][2] == 'app.integrations.chatgpt_assert_cli'
    assert captured['env']['CAE_CHATGPT_EXPECTED_BINDING'] == provider.binding()
    assert captured['env']['ASSERT_JUDGE_TIMEOUT_SECONDS'] == '300'
    assert 'access-secret' not in json.dumps(captured) and 'refresh-secret' not in json.dumps(result)
    assert 'account_binding_sha256' in result['judge_result']['provenance']['configuration']['transport']


def test_account_switch_between_model_selection_and_configuration_blocks_review(provider, monkeypatch, tmp_path):
    from test_upstream_assert_judge import _run_and_conversation, _scenario_contract, _configure_assert_runtime
    from app.services import upstream_assert_judge as judge
    authorize(provider, monkeypatch)
    monkeypatch.setattr(provider, 'list_models', lambda: [{'id': 'gpt-test'}])
    provider.select_judge_model('gpt-test')
    _configure_assert_runtime(monkeypatch, tmp_path)
    original = judge.judge_configuration
    def switch_account(model, judge_n):
        authorize(provider, monkeypatch, client_id='oaiapp_other')
        return original(model, judge_n)
    monkeypatch.setattr(judge, 'judge_configuration', switch_account)
    run, conversation = _run_and_conversation()
    with pytest.raises(judge.UpstreamAssertJudgeUnavailable, match='account changed'):
        judge.run_upstream_assert_judge(run=run, conversation=conversation,
            scenario_contract=_scenario_contract(), artifact_root=tmp_path / 'artifacts')
    assert not (tmp_path / 'artifacts').exists()


def test_profile_switch_after_request_claim_blocks_before_credits_or_inference(provider, monkeypatch, tmp_path):
    from fastapi import HTTPException
    from app.routes import assert_judge as route
    from app.services import upstream_assert_judge as judge
    from test_upstream_assert_judge import _run_and_conversation, _scenario_contract, _configure_assert_runtime
    _configure_assert_runtime(monkeypatch, tmp_path)
    authorize(provider, monkeypatch)
    monkeypatch.setattr(provider, 'list_models', lambda: [{'id': 'gpt-test'}])
    provider.select_judge_model('gpt-test')
    first_profile = provider.status()['active_profile_id']
    authorize(provider, monkeypatch, client_id='oaiapp_other')
    provider.select_judge_model('gpt-test')
    second_profile = provider.status()['active_profile_id']
    provider.select_profile(first_profile)
    run, conversation = _run_and_conversation()
    monkeypatch.setattr(route.execution_run_store, 'get_execution_run', lambda *_: run)
    monkeypatch.setattr(route.execution_run_store, 'get_conversation', lambda *_: conversation)
    monkeypatch.setattr(route, 'recorded_contract', lambda *_: _scenario_contract())
    claimed = {}
    def claim(db, **kwargs):
        claimed.update(kwargs)
        provider.select_profile(second_profile)
        return 'claimed-request', None
    monkeypatch.setattr(route.assert_judge_requests, 'claim', claim)
    failed = []
    monkeypatch.setattr(route.assert_judge_requests, 'failed', lambda db, invocation: failed.append(invocation))
    monkeypatch.setattr(route.assert_judge_requests, 'retain', lambda *_: pytest.fail('Retained wrong account response'))
    monkeypatch.setattr(judge, '_reserve_assert_credits', lambda **_: pytest.fail('Reserved credits after account switch'))
    monkeypatch.setattr(judge.subprocess, 'run', lambda *a, **kw: pytest.fail('Invoked wrong account'))
    with pytest.raises(HTTPException) as error:
        route.judge_execution_conversation(run['execution_run_id'], conversation['conversation_id'],
            route.AssertExecutionJudgeRequest(user_id=run['user_id']), db=object())
    assert error.value.status_code == 503
    assert 'after this request was claimed' in error.value.detail
    assert failed == ['claimed-request']
    current = judge.assert_judge_input_fingerprint(run=run, conversation=conversation,
        scenario_contract=_scenario_contract(), model='chatgpt_plan/gpt-test', judge_n=1)
    assert claimed['fingerprint'] != current


@pytest.mark.parametrize('denied', [False, True])
def test_real_callback_server_closes_without_joining_its_request_thread(provider, monkeypatch, denied):
    import threading
    monkeypatch.setattr(cp, 'CALLBACK_PORT', 0)
    monkeypatch.setenv('CHATGPT_PLAN_CALLBACK_BIND_HOST', '127.0.0.1')
    stopped = threading.Event()
    errors = []
    original_stop = provider._stop_listener
    def stop():
        try:
            original_stop()
        except Exception as exc:
            errors.append(exc)
        finally:
            stopped.set()
    monkeypatch.setattr(provider, '_stop_listener', stop)
    def callback(query):
        provider._pending = None
        if denied:
            raise cp.ChatGPTPlanError('Permission denied.')
    monkeypatch.setattr(provider, 'complete_callback', callback)
    provider._pending = {'attempt': 'synthetic'}
    cp.ChatGPTPlanProvider._start_listener(provider)
    server = provider._server
    monkeypatch.setattr(server, 'handle_error', lambda *_: errors.append('request-thread exception'))
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(f'http://127.0.0.1:{server.server_port}/auth/callback?state=synthetic')
        assert response.status_code == (400 if denied else 200)
        assert stopped.wait(5)
        assert not errors
        assert provider._server is None
        assert server.fileno() == -1
    finally:
        original_stop()
