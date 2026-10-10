from __future__ import annotations

import asyncio
import base64
import json
import time
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import waylo_connection as routes
from app.services import waylo_connection as connections
from app.services.waylo_login import (
    DEFAULT_WAYLO_API_BASE, MAX_RESPONSE_BYTES, WayloLogin, WayloLoginError,
    configured_waylo_api_base, login_waylo,
)


AGENT, WORKSPACE, TENANT = (str(uuid4()) for _ in range(3))
PASSWORD = 'private-password-never-returned'
HEADERS = {'Origin': 'http://127.0.0.1:3012', 'X-CAE-Waylo-Control': '1', 'Sec-Fetch-Site': 'same-origin'}


def token(exp: float | None = None) -> str:
    claims = json.dumps({'exp': time.time() + 600 if exp is None else exp}).encode()
    return 'eyJhbGciOiJIUzI1NiJ9.' + base64.urlsafe_b64encode(claims).decode().rstrip('=') + '.test-signature'


def body(**overrides):
    return {'email': 'caller@example.test', 'password': PASSWORD, 'waylo_agent_id': AGENT,
            'confirm': True, 'expected_endpoint_url': DEFAULT_WAYLO_API_BASE, **overrides}


@pytest.fixture(autouse=True)
def fixed_provider_environment(monkeypatch):
    for name in ('CAE_WAYLO_API_BASE_URL', 'CAE_WAYLO_ALLOWED_API_ORIGINS', 'CAE_WAYLO_ALLOWED_ORIGINS'):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def route_client():
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def provider_handler(calls: list, *, platform=False, mismatch=False):
    access = token()

    def handler(request: httpx.Request):
        calls.append(request)
        assert 'cookie' not in request.headers
        if request.url.path == '/auth/login':
            assert request.method == 'POST'
            assert json.loads(request.content) == {'email': 'caller@example.test', 'password': PASSWORD}
            assert 'authorization' not in request.headers
            return httpx.Response(200, json={'token': access, 'user': {'email': 'do-not-export@example.test'}},
                                  headers={'Set-Cookie': 'rt=refresh-secret; Path=/; Secure; HttpOnly'})
        assert request.headers['authorization'] == f'Bearer {access}'
        if request.url.path == '/auth/me':
            return httpx.Response(200, json={'isPlatformAdmin': platform, 'tenantId': None if platform else TENANT,
                                            'tenantRole': None if platform else 'owner'},
                                  headers={'Set-Cookie': 'rt=another-refresh-secret; Path=/; Secure; HttpOnly'})
        expected = f'/admin/tenants/{TENANT}/agents/{AGENT}' if platform else f'/agents/{AGENT}'
        assert request.url.path == expected
        return httpx.Response(200, json={'id': str(uuid4()) if mismatch else AGENT,
                                        'workspaceId': WORKSPACE, 'name': 'Mike'})
    return handler, access


def test_password_login_verifies_authority_and_discards_refresh_cookie():
    calls = []
    handler, access = provider_handler(calls)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = asyncio.run(login_waylo('caller@example.test', PASSWORD, AGENT, client=client))
    assert result == WayloLogin(access, DEFAULT_WAYLO_API_BASE, WORKSPACE, AGENT, 'Mike')
    assert [request.url.path for request in calls] == ['/auth/login', '/auth/me', f'/agents/{AGENT}']
    assert not client.cookies
    assert access not in repr(result) and PASSWORD not in repr(result)
    asyncio.run(client.aclose())


def test_platform_admin_uses_only_explicit_tenant_mount():
    calls = []
    handler, _ = provider_handler(calls, platform=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = asyncio.run(login_waylo('caller@example.test', PASSWORD, AGENT, TENANT, client=client))
    assert result.endpoint_url == f'{DEFAULT_WAYLO_API_BASE}/admin/tenants/{TENANT}'
    assert len(calls) == 3 and not client.cookies
    asyncio.run(client.aclose())


@pytest.mark.parametrize('platform,tenant,expected', [(True, None, 'explicitly select'), (False, TENANT, 'only to')])
def test_tenant_context_never_brute_forces_other_tenants(platform, tenant, expected):
    calls = []
    handler, _ = provider_handler(calls, platform=platform)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WayloLoginError, match=expected):
        asyncio.run(login_waylo('caller@example.test', PASSWORD, AGENT, tenant, client=client))
    assert len(calls) == 2 and not client.cookies
    asyncio.run(client.aclose())


def test_provider_agent_must_match_selected_agent():
    calls = []
    handler, _ = provider_handler(calls, mismatch=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WayloLoginError, match='different agent'):
        asyncio.run(login_waylo('caller@example.test', PASSWORD, AGENT, client=client))
    assert not client.cookies
    asyncio.run(client.aclose())


@pytest.mark.parametrize('status', [301, 302, 307, 401, 403, 429, 500])
def test_upstream_refusals_and_redirects_do_not_leak_or_follow_credentials(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={'error': PASSWORD}, headers={
            'Location': f'https://evil.test/{PASSWORD}', 'Set-Cookie': 'rt=refresh-secret; Path=/; Secure'})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WayloLoginError) as error:
        asyncio.run(login_waylo('caller@example.test', PASSWORD, AGENT, client=client))
    assert PASSWORD not in str(error.value) and 'evil.test' not in str(error.value)
    assert len(calls) == 1 and not client.cookies
    asyncio.run(client.aclose())


@pytest.mark.parametrize('failure_step,expected_phase', [
    (1, 'password sign-in'), (2, 'session verification'), (3, 'selected-agent access'),
])
@pytest.mark.parametrize('status', [401, 403])
def test_phase_specific_refusal_is_safe_stops_and_discards_cookies(route_client, monkeypatch,
                                                                 failure_step, expected_phase, status):
    calls = []
    successful_handler, access = provider_handler([])
    def handler(request):
        calls.append(request)
        assert 'cookie' not in request.headers
        if len(calls) == failure_step:
            return httpx.Response(status, json={'error': {
                'password': PASSWORD, 'token': access, 'email': 'caller@example.test',
                'path': str(request.url), 'agent': AGENT, 'workspace': WORKSPACE, 'tenant': TENANT,
            }}, headers={'Set-Cookie': 'rt=refused-refresh-secret; Path=/; Secure; HttpOnly'})
        return successful_handler(request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async def with_mock_provider(email, password, agent, tenant_id=None, **kwargs):
        return await login_waylo(email, password, agent, tenant_id, client=client, **kwargs)
    monkeypatch.setattr(routes, 'login_waylo', with_mock_provider)
    try:
        response = route_client.post('/api/waylo/connection/connect', json=body(), headers=HEADERS)
        # Existing local API/frontend contract is unchanged; only safe detail improves.
        assert response.status_code == 400
        detail = response.json()['detail']
        assert expected_phase in detail and f'HTTP {status}' in detail
        assert 'wrong password' not in detail.lower()
        assert 'no-store' in response.headers['cache-control'] and 'set-cookie' not in response.headers
        assert len(calls) == failure_step and not client.cookies
        for private in (PASSWORD, access, 'caller@example.test', AGENT, WORKSPACE, TENANT,
                        '/auth/login', '/auth/me', '/agents/', 'refused-refresh-secret'):
            assert private not in response.text
    finally:
        asyncio.run(client.aclose())


def test_transport_failure_never_exposes_password_or_provider_url():
    def handler(request):
        raise httpx.ConnectError(f'failure {PASSWORD}', request=request)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(WayloLoginError) as error:
        asyncio.run(login_waylo('caller@example.test', PASSWORD, AGENT, client=client))
    assert PASSWORD not in str(error.value) and not client.cookies
    asyncio.run(client.aclose())


@pytest.mark.parametrize('response', [httpx.Response(200, json=[]), httpx.Response(200, text=PASSWORD),
                                     httpx.Response(200, content=b'x' * (MAX_RESPONSE_BYTES + 1))])
def test_invalid_login_response_is_safe(response):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(WayloLoginError) as error:
        asyncio.run(login_waylo('caller@example.test', PASSWORD, AGENT, client=client))
    assert PASSWORD not in str(error.value) and not client.cookies
    asyncio.run(client.aclose())


@pytest.mark.parametrize('base', ['http://api.poc.app.waylovoice.ai', 'https://evil.test',
                                  'https://api.poc.app.waylovoice.ai/auth', 'https://u:p@api.poc.app.waylovoice.ai',
                                  'https://api.poc.app.waylovoice.ai?secret=bad'])
def test_login_endpoint_is_operator_configured_and_allowlisted(monkeypatch, base):
    monkeypatch.setenv('CAE_WAYLO_API_BASE_URL', base)
    with pytest.raises(WayloLoginError):
        configured_waylo_api_base()


def test_operator_can_configure_a_different_allowed_https_origin(monkeypatch):
    monkeypatch.setenv('CAE_WAYLO_API_BASE_URL', 'https://api.waylo.test/')
    monkeypatch.setenv('CAE_WAYLO_ALLOWED_API_ORIGINS', 'https://api.waylo.test')
    assert configured_waylo_api_base() == 'https://api.waylo.test'


def test_config_reports_only_actual_default_login_recipient_no_store(route_client):
    response = route_client.get('/api/waylo/connection/config', headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {'endpoint_url': DEFAULT_WAYLO_API_BASE}
    assert response.headers['cache-control'] == 'no-store, private'
    assert 'set-cookie' not in response.headers


def test_config_reports_operator_custom_login_recipient(route_client, monkeypatch):
    monkeypatch.setenv('CAE_WAYLO_API_BASE_URL', 'https://api.waylo.test/')
    monkeypatch.setenv('CAE_WAYLO_ALLOWED_API_ORIGINS', 'https://api.waylo.test')
    response = route_client.get('/api/waylo/connection/config', headers=HEADERS)
    assert response.status_code == 200 and response.json() == {'endpoint_url': 'https://api.waylo.test'}


@pytest.mark.parametrize('headers', [{}, {**HEADERS, 'Origin': 'https://evil.test'},
                                   {**HEADERS, 'Sec-Fetch-Site': 'cross-site'}])
def test_config_requires_local_browser_control(route_client, headers):
    response = route_client.get('/api/waylo/connection/config', headers=headers)
    assert response.status_code == 403 and 'no-store' in response.headers['cache-control']


def test_invalid_provider_config_is_static_503_without_configured_secrets(route_client, monkeypatch):
    monkeypatch.setenv('CAE_WAYLO_API_BASE_URL', f'https://user:{PASSWORD}@evil.test')
    response = route_client.get('/api/waylo/connection/config', headers=HEADERS)
    assert response.status_code == 503
    assert PASSWORD not in response.text and 'evil.test' not in response.text
    assert 'no-store' in response.headers['cache-control']


@pytest.mark.parametrize('expected', ['https://old.waylo.test', 'https://evil.test', 'not-a-url',
                                     f'https://{PASSWORD}@api.poc.app.waylovoice.ai', 'https://api.poc.app.waylovoice.ai/'])
def test_stale_or_different_recipient_consent_never_forwards_password(route_client, monkeypatch, expected):
    async def never_login(*args, **kwargs):
        pytest.fail('Mismatched recipient must be refused before any password is forwarded.')
    monkeypatch.setattr(routes, 'login_waylo', never_login)
    response = route_client.post('/api/waylo/connection/connect', json=body(expected_endpoint_url=expected), headers=HEADERS)
    assert response.status_code == 400 and 'reload and consent again' in response.text
    assert PASSWORD not in response.text and expected not in response.text


def test_helper_rechecks_consent_if_server_config_changes_before_upstream_call(monkeypatch):
    monkeypatch.setenv('CAE_WAYLO_API_BASE_URL', 'https://api.waylo.test')
    monkeypatch.setenv('CAE_WAYLO_ALLOWED_API_ORIGINS', 'https://api.waylo.test')
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail('No password forwarding.')))
    with pytest.raises(WayloLoginError, match='reload and consent again'):
        asyncio.run(login_waylo('caller@example.test', PASSWORD, AGENT, client=client,
                               expected_endpoint_url=DEFAULT_WAYLO_API_BASE))
    asyncio.run(client.aclose())


@pytest.mark.parametrize('origin', ['https://evil.test:443', 'http://localhost', 'http://127.0.0.1:3012/path',
                                   'http://localhost:3012?x=1', 'http://u:p@127.0.0.1:3012', 'null'])
def test_origin_configuration_cannot_enable_nonlocal_control(route_client, monkeypatch, origin):
    monkeypatch.setenv('CAE_WAYLO_ALLOWED_ORIGINS', origin)
    response = route_client.post('/api/waylo/connection/connect', json=body(), headers={**HEADERS, 'Origin': origin})
    assert response.status_code == 503 and 'loopback origins' in response.text
    assert PASSWORD not in response.text


@pytest.mark.parametrize('headers', [{}, {'X-CAE-Waylo-Control': '1'},
    {**HEADERS, 'Origin': 'null'}, {**HEADERS, 'Origin': 'https://evil.test'},
    {**HEADERS, 'Sec-Fetch-Site': 'cross-site'}, {**HEADERS, 'Sec-Fetch-Site': 'same-site'},
    {**HEADERS, 'Origin': 'https://evil.test', 'X-Forwarded-Host': '127.0.0.1:3012'}])
def test_connect_requires_intentional_trusted_origin(route_client, headers):
    response = route_client.post('/api/waylo/connection/connect', json=body(), headers=headers)
    assert response.status_code == 403
    assert response.headers['cache-control'] == 'no-store, private'
    assert PASSWORD not in response.text


@pytest.mark.parametrize('overrides', [{'confirm': False}, {'confirm': 1}, {'confirm': 'true'},
    {'password': {}}, {'password': ''}, {'email': []}, {'waylo_agent_id': 'not-a-uuid'},
    {'tenant_id': 'not-a-uuid'}, {'endpoint_url': f'https://evil.test/{PASSWORD}'},
    {'expected_endpoint_url': None}, {'expected_endpoint_url': ''}, {'expected_endpoint_url': []}])
def test_input_validation_never_echoes_password(route_client, monkeypatch, overrides):
    async def never_login(*args, **kwargs):
        pytest.fail('Invalid input must be refused before upstream login.')
    monkeypatch.setattr(routes, 'login_waylo', never_login)
    response = route_client.post('/api/waylo/connection/connect', json=body(**overrides), headers=HEADERS)
    assert response.status_code == 400
    assert PASSWORD not in response.text and 'input' not in response.json()


def test_oversized_or_invalid_body_is_generic_no_store(route_client):
    for content in ('{"password":', 'x' * (routes.MAX_BODY_BYTES + 1), '[' * 1200 + ']' * 1200):
        response = route_client.post('/api/waylo/connection/connect', content=content,
                                     headers={**HEADERS, 'Content-Type': 'application/json'})
        assert response.status_code == 400 and 'no-store' in response.headers['cache-control']
        assert content not in response.text


def test_connect_status_disconnect_share_only_memory_proof(route_client, monkeypatch):
    access = token()
    async def fake_login(*args, **kwargs):
        assert args == ('caller@example.test', PASSWORD, AGENT, None)
        assert kwargs == {'expected_endpoint_url': DEFAULT_WAYLO_API_BASE}
        return WayloLogin(access, DEFAULT_WAYLO_API_BASE, WORKSPACE, AGENT, 'Mike')
    monkeypatch.setattr(routes, 'login_waylo', fake_login)
    response = route_client.post('/api/waylo/connection/connect', json=body(), headers=HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result['connected'] is True and result['agent_name'] == 'Mike'
    assert result['workspace_id'] == WORKSPACE and result['waylo_agent_id'] == AGENT
    assert access not in response.text and PASSWORD not in response.text and 'set-cookie' not in response.headers
    proof = result['session_proof']
    try:
        private_headers = {**HEADERS, 'X-CAE-Waylo-Session': proof}
        status = route_client.get('/api/waylo/connection/status', headers=private_headers)
        assert status.json()['connected'] is True and proof not in status.text
        assert 'no-store' in status.headers['cache-control']
        disconnected = route_client.post('/api/waylo/connection/disconnect', headers=private_headers)
        assert disconnected.json() == {'connected': False}
        assert route_client.get('/api/waylo/connection/status', headers=private_headers).json()['connected'] is False
    finally:
        connections.disconnect(proof)


def test_successful_reconnect_revokes_previous_browser_grant_and_run(route_client, monkeypatch):
    access = token()
    previous, _ = connections.connect(access, DEFAULT_WAYLO_API_BASE, WORKSPACE, AGENT)
    target = {'target': 'waylo', 'connection': {'endpoint_url': DEFAULT_WAYLO_API_BASE,
               'workspace_id': WORKSPACE, 'waylo_agent_id': AGENT}}
    old_lease = connections.authorize(previous, target)
    async def fake_login(*args, **kwargs):
        return WayloLogin(access, DEFAULT_WAYLO_API_BASE, WORKSPACE, AGENT, 'Mike')
    monkeypatch.setattr(routes, 'login_waylo', fake_login)
    response = route_client.post('/api/waylo/connection/connect', json=body(), headers={
        **HEADERS, 'X-CAE-Waylo-Session': previous})
    assert response.status_code == 200
    proof = response.json()['session_proof']
    try:
        assert proof != previous and old_lease.revoked.is_set()
        assert connections.status(previous) == {'connected': False}
        assert connections.status(proof)['connected'] is True
    finally:
        connections.disconnect(previous)
        connections.disconnect(proof)


def test_failed_reconnect_preserves_previous_grant(route_client, monkeypatch):
    previous, _ = connections.connect(token(), DEFAULT_WAYLO_API_BASE, WORKSPACE, AGENT)
    async def failed_login(*args, **kwargs):
        raise WayloLoginError('Waylo sign-in or access was refused.')
    monkeypatch.setattr(routes, 'login_waylo', failed_login)
    try:
        response = route_client.post('/api/waylo/connection/connect', json=body(), headers={
            **HEADERS, 'X-CAE-Waylo-Session': previous})
        assert response.status_code == 400 and connections.status(previous)['connected'] is True
    finally:
        connections.disconnect(previous)


def test_status_requires_custom_control_and_header_proof_not_query(route_client):
    assert route_client.get('/api/waylo/connection/status', headers=HEADERS,
                            params={'session_proof': 'secret'}).status_code == 401
    response = route_client.get('/api/waylo/connection/status', headers={
        'X-CAE-Waylo-Control': '1', 'Sec-Fetch-Site': 'same-origin', 'X-CAE-Waylo-Session': 'unknown'})
    assert response.status_code == 200 and response.json() == {'connected': False}
    assert route_client.get('/api/waylo/connection/status', headers={
        'X-CAE-Waylo-Control': '1', 'X-CAE-Waylo-Session': 'unknown'}).status_code == 403


def test_api_login_error_is_safe_and_does_not_store(route_client, monkeypatch):
    async def failed_login(*args, **kwargs):
        raise WayloLoginError('Waylo sign-in or access was refused.')
    monkeypatch.setattr(routes, 'login_waylo', failed_login)
    response = route_client.post('/api/waylo/connection/connect', json=body(), headers=HEADERS)
    assert response.status_code == 400
    assert PASSWORD not in response.text and 'no-store' in response.headers['cache-control']


def test_expired_provider_token_never_connects(route_client, monkeypatch):
    async def expired_login(*args, **kwargs):
        return WayloLogin(token(time.time() - 1), DEFAULT_WAYLO_API_BASE, WORKSPACE, AGENT, 'Mike')
    monkeypatch.setattr(routes, 'login_waylo', expired_login)
    response = route_client.post('/api/waylo/connection/connect', json=body(), headers=HEADERS)
    assert response.status_code == 400 and 'unexpired' in response.text and PASSWORD not in response.text
