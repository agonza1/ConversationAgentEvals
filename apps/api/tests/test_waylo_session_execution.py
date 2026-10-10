from __future__ import annotations

import asyncio
import base64
import json
import time
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import get_db
from app.routes import execution as execution_routes, agents as agent_routes
from app.schemas.agents import AgentCreateRequest
from app.schemas.execution import ExecutionRunCreateRequest
from app.services import agent_store, execution_run_store, execution_runner, waylo_connection
from app.services.waylo_target import WayloClient, WayloError
from app.services.waylo_livekit import run_waylo_call


WORKSPACE, AGENT = str(uuid4()), str(uuid4())
TARGET = {'id': 'temporary-mike', 'name': 'Mike', 'channel': 'voice', 'target': 'waylo',
          'connection': {'endpoint_url': 'https://api.poc.app.waylovoice.ai',
                         'workspace_id': WORKSPACE, 'waylo_agent_id': AGENT,
                         'auth_type': 'waylo_browser_session'}}
HEADERS = {'Origin': 'http://127.0.0.1:3012', 'X-CAE-Waylo-Control': '1',
           'Sec-Fetch-Site': 'same-origin'}


def jwt(seconds=600):
    body = base64.urlsafe_b64encode(json.dumps({'exp': time.time() + seconds}).encode()).decode().rstrip('=')
    return f'header.{body}.signature'


@pytest.fixture
def connected(tmp_path, monkeypatch):
    monkeypatch.setattr(waylo_connection, 'connection_store', waylo_connection.WayloConnectionStore(timer_factory=None))
    monkeypatch.setattr(agent_store, 'get_agent', lambda ident: deepcopy(TARGET) if ident == TARGET['id'] else None)
    monkeypatch.setattr(execution_runner, 'get_agent', agent_store.get_agent)
    monkeypatch.setattr(execution_run_store, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(execution_run_store, 'REPO_ROOT', tmp_path)
    execution_run_store.reset_execution_runs_for_tests()
    monkeypatch.setattr(execution_routes, 'resolve_execution_product_project_id', lambda **kw: None)
    monkeypatch.setattr(execution_routes, 'prepare_execution_reference_models', lambda payload: payload)
    app.dependency_overrides[get_db] = lambda: None
    token = jwt()
    proof, _ = waylo_connection.connect(token, TARGET['connection']['endpoint_url'], WORKSPACE, AGENT)
    yield token, proof, TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    waylo_connection.connection_store.clear()
    execution_run_store.reset_execution_runs_for_tests()


def run_payload(**extra):
    return {'agent_id': TARGET['id'], 'suite_id': 'waylo-mike-notes',
            'scenario_ids': ['mike-five-bags'], 'mode': 'pipecat_webrtc',
            'executor_id': 'waylo_livekit', **extra}


def test_temporary_schema_never_accepts_secret_ref_or_other_target():
    assert AgentCreateRequest.model_validate(TARGET).connection.secret_ref is None
    for change in ({'secret_ref': 'server-key'}, {'access_token': jwt()}):
        bad = deepcopy(TARGET)
        bad['connection'].update(change)
        with pytest.raises(ValueError):
            AgentCreateRequest.model_validate(bad)
    bad = deepcopy(TARGET)
    bad.update(target='http_endpoint', channel='text')
    with pytest.raises(ValueError):
        AgentCreateRequest.model_validate(bad)


@pytest.mark.parametrize('operation', ['run', 'capture', 'readiness'])
def test_existing_routes_require_browser_proof_and_local_control(connected, operation):
    _, proof, client = connected
    url = {'run': '/api/execution/runs', 'capture': f'/api/agents/{TARGET["id"]}/capture',
           'readiness': f'/api/agents/{TARGET["id"]}/readiness'}[operation]
    data = run_payload() if operation == 'run' else {'session_id': str(uuid4())}
    for headers, expected in (({}, 403), (HEADERS, 401), ({**HEADERS, 'X-CAE-Waylo-Session': 'wrong'}, 401),
                              ({**HEADERS, 'X-CAE-Waylo-Session': proof, 'Origin': 'https://evil.test'}, 403)):
        response = client.get(url, headers=headers) if operation == 'readiness' else client.post(url, json=data, headers=headers)
        assert response.status_code == expected
        assert proof not in response.text
    assert execution_run_store.list_execution_runs(user_id='execution-user') == []


def test_queue_authorization_is_memory_only_and_released(connected, monkeypatch, tmp_path):
    token, proof, client = connected
    checked = []
    def execute(run_id, payload):
        lease = waylo_connection.run_lease(run_id, TARGET)
        assert lease.token() == token
        checked.append(run_id)
        waylo_connection.release_run(run_id)
    monkeypatch.setattr(execution_routes, 'execute_execution_run', execute)
    response = client.post('/api/execution/runs', json=run_payload(),
                           headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == 200, response.text
    assert checked == [response.json()['execution_run_id']]
    serialized = response.text + ''.join(path.read_text() for path in tmp_path.rglob('*.json'))
    assert token not in serialized and proof not in serialized
    assert 'waylo_browser_session' in serialized
    with pytest.raises(waylo_connection.WayloConnectionError):
        waylo_connection.run_lease(checked[0], TARGET)


def test_queue_rejects_budget_and_disconnected_grants_without_persisting(connected):
    _, proof, client = connected
    response = client.post('/api/execution/runs', json=run_payload(iterations=10),
                           headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == 400 and 'expires' in response.text
    waylo_connection.disconnect(proof)
    assert client.post('/api/execution/runs', json=run_payload(),
                       headers={**HEADERS, 'X-CAE-Waylo-Session': proof}).status_code == 401
    assert execution_run_store.list_execution_runs(user_id='execution-user') == []


def test_disconnect_queue_binding_race_is_failed_not_orphaned(connected, monkeypatch):
    _, proof, client = connected
    def fail_bind(run_id, lease):
        waylo_connection.disconnect(proof)
        raise waylo_connection.WayloConnectionError('Waylo access expired.')
    monkeypatch.setattr(waylo_connection, 'bind_run', fail_bind)
    response = client.post('/api/execution/runs', json=run_payload(),
                           headers={**HEADERS, 'X-CAE-Waylo-Session': proof})
    assert response.status_code == 400
    runs = execution_run_store.list_execution_runs(user_id='execution-user')
    assert len(runs) == 1 and runs[0]['status'] == 'failed'


def test_http_retries_check_revocation_and_401_revokes(connected):
    _, proof, _ = connected
    lease = waylo_connection.authorize(proof, TARGET)
    calls = []
    def handler(request):
        calls.append(request)
        waylo_connection.disconnect(proof)
        return httpx.Response(503)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WayloClient(TARGET, client=http, credential_lease=lease)
            assert client._secret is None
            with pytest.raises(waylo_connection.WayloConnectionError):
                await client.request('GET', 'agents')
    asyncio.run(exercise())
    assert len(calls) == 1
    new_proof, _ = waylo_connection.connect(jwt(), TARGET['connection']['endpoint_url'], WORKSPACE, AGENT)
    rejected = waylo_connection.authorize(new_proof, TARGET)
    async def unauthorized():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401))) as http:
            client = WayloClient(TARGET, client=http, credential_lease=rejected)
            with pytest.raises(WayloError, match='revoked'):
                await client.request('GET', 'agents')
    asyncio.run(unauthorized())
    assert rejected.revoked.is_set()


def test_transport_rejects_scope_changes_and_discards_cookies(connected):
    _, proof, _ = connected
    lease = waylo_connection.authorize(proof, TARGET)
    for field, value in [('endpoint_url', 'https://evil.test'), ('workspace_id', str(uuid4())),
                         ('waylo_agent_id', str(uuid4()))]:
        changed = deepcopy(TARGET)
        changed['connection'][field] = value
        with pytest.raises(waylo_connection.WayloConnectionError):
            WayloClient(changed, credential_lease=lease)
    calls = []
    def handler(request):
        calls.append(request)
        assert 'cookie' not in request.headers
        return httpx.Response(200, json={}, headers={'Set-Cookie': 'refresh=not-to-be-reused; Path=/'})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            http.cookies.set('existing', 'ambient-cookie')
            client = WayloClient(TARGET, client=http, credential_lease=lease)
            await client.request('GET', 'agents')
            await client.request('GET', 'agents')
            assert not http.cookies
    asyncio.run(exercise())
    assert len(calls) == 2


def test_target_authentication_change_cannot_fall_back_to_server_key(connected, monkeypatch):
    _, proof, _ = connected
    lease = waylo_connection.authorize(proof, TARGET)
    changed = deepcopy(TARGET)
    changed['connection'].update(auth_type='bearer_secret', secret_ref='existing-server-key')
    monkeypatch.setattr(execution_runner, 'get_agent', lambda _: changed)
    monkeypatch.setattr(execution_runner, 'resolve_http_target_secret', lambda _: pytest.fail('No server fallback'))
    with pytest.raises(ValueError, match='authentication changed'):
        execution_runner.start_execution_run(ExecutionRunCreateRequest.model_validate(run_payload()), waylo_lease=lease)


def test_disconnect_cancels_active_rtc_and_closes_client(connected, monkeypatch, tmp_path):
    from app.services import waylo_livekit
    _, proof, _ = connected
    lease = waylo_connection.authorize(proof, TARGET)
    flags = []
    class Client:
        async def close(self):
            flags.append('client-closed')
    async def active_call(**kw):
        try:
            waylo_connection.disconnect(proof)
            await asyncio.sleep(10)
        finally:
            flags.append('rtc-closed')
    monkeypatch.setattr(waylo_livekit, '_run_waylo_call', active_call)
    async def exercise():
        with pytest.raises(waylo_connection.WayloConnectionError):
            await asyncio.wait_for(run_waylo_call(target=TARGET, correlation_id='test', scenario={},
                suite_id='waylo-mike-notes', artifact_dir=tmp_path, max_exchanges=1, timeout_seconds=30,
                next_utterance=None, synthesize=None, client=Client(), credential_lease=lease), 2)
    asyncio.run(exercise())
    assert flags == ['rtc-closed', 'client-closed']
