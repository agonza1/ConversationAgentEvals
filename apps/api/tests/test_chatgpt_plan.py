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


def test_real_litellm_and_assert_structured_transport_use_responses(provider, monkeypatch):
    authorize(provider, monkeypatch)
    from assert_ai.core.model_client import generate_structured, GenerateOptions
    payloads = []
    def handler(request):
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
