"""Hosted boundary tests use synthetic Firebase identities and never contact a paid provider."""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db import database
from app.security import identity, request_maps
from app.services import firebase_auth


@pytest.fixture
def hosted_app(monkeypatch, tmp_path):
    database_url = os.getenv('CAE_TEST_POSTGRES_URL')
    engine = create_engine(database_url) if database_url else create_engine(f'sqlite:///{tmp_path}/hosted.db', connect_args={'check_same_thread': False})
    if database_url: database.Base.metadata.drop_all(engine)
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for module in list(sys.modules.values()):
        if getattr(module, '__name__', '').startswith('app.') and hasattr(module, 'SessionLocal'):
            monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setenv('CAE_AUTH_MODE', 'firebase')
    monkeypatch.setenv('CAE_PUBLIC_ORIGIN', 'https://staging.example')
    monkeypatch.setenv('CAE_CREDENTIAL_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('CAE_REQUEST_RATE_PER_MINUTE', '1000')
    monkeypatch.setenv('CAE_WRITE_RATE_PER_MINUTE', '1000')
    monkeypatch.setenv('CAE_COMPUTE_RATE_PER_MINUTE', '1000')
    monkeypatch.setenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '1')

    def claims(cookie):
        if cookie not in {'alice', 'bob'}: raise ValueError('Invalid session')
        return {'sub': cookie, 'email': f'{cookie}@example.com', 'email_verified': True,
                'firebase': {'sign_in_provider': 'google.com'}}
    monkeypatch.setattr(firebase_auth, 'verify_session', claims)
    monkeypatch.setattr(firebase_auth, 'create_session', lambda token: (token, claims(token)))
    def client(uid=None):
        headers = {'Origin': 'https://staging.example'}
        if uid: headers['Cookie'] = f'__session={uid}'
        return TestClient(app, headers=headers)
    yield client, factory
    assert identity.get() is None
    assert request_maps.get() is None
    engine.dispose()


def test_auth_session_csrf_and_fail_closed(hosted_app):
    client, _ = hosted_app
    assert client().get('/health').status_code == 200
    assert client().get('/api/auth/config').json()['enabled']
    for path in ('/api/agents', '/api/scenarios', '/api/product/projects', '/storage/private.wav', '/docs'):
        assert client().get(path).status_code == 401
    assert client('forged').get('/api/agents').status_code == 401
    signed = client()
    result = signed.post('/api/auth/session', json={'id_token': 'alice'})
    assert result.status_code == 200
    assert 'httponly' in result.headers['set-cookie'].lower()
    assert 'secure' in result.headers['set-cookie'].lower()
    assert result.headers['cache-control'] == 'no-store'
    assert client('alice').get('/api/auth/me').json()['uid'] == 'alice'
    assert client('alice').post('/api/agents', headers={'Origin': 'https://evil.example'}, json={}).status_code == 403
    assert client('alice').delete('/api/auth/session').status_code == 200
    malformed = client('alice').put('/api/auth/openai-key', json={'api_key': 'sk-secret'})
    assert malformed.status_code == 422
    assert 'sk-secret' not in malformed.text
    assert client().post('/api/auth/session', json={'id_token': 'x' * 40000}).status_code == 413


def test_private_projects_agents_scenarios_settings_and_catalog(hosted_app):
    client, _ = hosted_app
    alice, bob = client('alice'), client('bob')
    project = alice.post('/api/product/projects', json={'user_id': 'bob', 'project_id': 'private', 'name': 'Alice private'} )
    assert project.status_code == 200, project.text
    assert project.json()['user_id'] == 'alice'
    assert bob.get('/api/product/projects?user_id=alice').json() == []
    agent = alice.post('/api/agents', json={'id': 'same-id', 'name': 'Alice agent', 'target': 'openai_codex'})
    assert agent.status_code == 200, agent.text
    assert bob.get('/api/agents/same-id').status_code == 404
    assert bob.patch('/api/agents/same-id', json={'name': 'Stolen'}).status_code == 404
    assert bob.delete('/api/agents/same-id').status_code == 404
    assert bob.post('/api/agents', json={'id': 'same-id', 'name': 'Bob agent', 'target': 'openai_codex'}).status_code == 200
    scenario = alice.post('/api/scenarios', json={'title': 'Alice secret', 'prompt': 'Private prompt', 'expected_output': 'Private result', 'description': 'Private description'})
    assert scenario.status_code == 200, scenario.text
    scenario_id = scenario.json()['id']
    assert bob.get(f'/api/scenarios/{scenario_id}').status_code == 404
    assert bob.get('/api/scenarios').json()['scenarios'] == []
    assert 'Alice secret' not in bob.get('/api/benchmarks').text
    assert 'Alice secret' in alice.get('/api/benchmarks').text
    assert alice.patch('/api/specs/generation-settings', json={'model': 'gpt-4.1'}).status_code == 200
    assert bob.get('/api/specs/generation-settings').json()['model'] is None
    assert alice.get('/api/specs/generation-settings').json()['model'] == 'gpt-4.1'


def test_keys_encrypted_no_fallback_and_concurrent_provider_identity(hosted_app, monkeypatch):
    client, factory = hosted_app
    from app.models.hosted import PrivateDocument
    from app.services.llm_providers import get_provider
    from app.services.user_credentials import provider_environment
    # Even an accidentally present operator key is never selected at runtime.
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-operator-never-use')
    assert not client('alice').get('/api/auth/openai-key').json()['configured']
    assert not client('alice').get('/api/specs/generation-settings').json()['available']
    token = identity.set('alice')
    try:
        from app.services.editable_assert_spec import _complete_generation, SpecGenerationUnavailable
        with pytest.raises(SpecGenerationUnavailable, match='API key'): _complete_generation('No operator fallback')
    finally: identity.reset(token)
    assert client('alice').put('/api/auth/openai-key', json={'api_key': 'sk-alice-private-key'}).status_code == 200
    assert not client('bob').get('/api/auth/openai-key').json()['configured']
    assert client('bob').put('/api/auth/openai-key', json={'api_key': 'sk-bob-private-key'}).status_code == 200
    with factory() as db:
        stored = db.execute(select(PrivateDocument.__table__.c.value)).scalars().all()
        assert 'sk-alice-private-key' not in str(stored)
        assert 'sk-bob-private-key' not in str(stored)
    import httpx
    sent = []
    def fake_post(url, **kwargs):
        assert url == 'https://api.openai.com/v1/responses'
        sent.append(kwargs['headers']['Authorization'])
        return httpx.Response(200, request=httpx.Request('POST', url), json={'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Synthetic reply'}]}]})
    monkeypatch.setattr(httpx, 'post', fake_post)
    def complete(uid):
        token = identity.set(uid)
        try:
            env = provider_environment()
            assert env['OPENAI_API_KEY'] == f'sk-{uid}-private-key'
            assert 'CAE_CREDENTIAL_ENCRYPTION_KEY' not in env
            assert env['CAE_AUTH_MODE'] == 'local'  # Trusted text-only child, with no user DB credentials.
            return get_provider().complete('Synthetic test', model_name='gpt-4.1-mini')
        finally: identity.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(complete, ['alice', 'bob'])) == ['Synthetic reply', 'Synthetic reply']
    assert sorted(sent) == ['Bearer sk-alice-private-key', 'Bearer sk-bob-private-key']
    assert client('alice').delete('/api/auth/openai-key').status_code == 200
    token = identity.set('alice')
    try:
        with pytest.raises(ValueError, match='API key'): get_provider().complete('No key')
    finally: identity.reset(token)
    assert client('bob').get('/api/auth/openai-key').json()['configured']


def test_sample_execution_and_evidence_are_private_and_free(hosted_app, monkeypatch):
    client, _ = hosted_app
    import httpx
    monkeypatch.setattr(httpx, 'post', lambda *args, **kwargs: pytest.fail('No paid call is allowed'))
    alice, bob = client('alice'), client('bob')
    result = alice.post('/api/execution/runs', json={'suite_id': 'call-center-voice-ai', 'scenario_ids': ['billing-address-change'], 'user_id': 'bob'})
    assert result.status_code == 200, result.text
    run_id = result.json()['execution_run_id']
    saved = alice.get(f'/api/execution/runs/{run_id}?user_id=bob')
    assert saved.status_code == 200, saved.text
    assert saved.json()['user_id'] == 'alice'
    assert saved.json()['status'] not in {'running', 'queued', 'failed'}
    assert bob.get(f'/api/execution/runs/{run_id}?user_id=alice').status_code == 404
    assert run_id not in bob.get('/api/execution/runs?user_id=alice').text
    report = alice.post('/api/benchmarks/run', json={'suite_id': 'call-center-voice-ai', 'scenario_id': 'billing-address-change', 'user_id': 'bob', 'transcript': 'User: Change address. Agent: Please verify your account.'})
    assert report.status_code == 200, report.text
    benchmark_id = report.json()['run_id']
    assert bob.get(f'/api/benchmarks/runs/{benchmark_id}?user_id=alice').status_code == 404
    assert not alice.get('/api/assert/readiness').json()['ready']
    for path in ('/storage/private.wav', '/api/product/providers/openai/oauth/start', '/api/execution/reference/complete'):
        response = alice.get(path) if path.startswith('/storage') else alice.post(path, json={})
        assert response.status_code == 403


def test_database_rate_limits_survive_clients_and_are_user_scoped(hosted_app, monkeypatch):
    client, _ = hosted_app
    monkeypatch.setenv('CAE_REQUEST_RATE_PER_MINUTE', '2')
    assert client('alice').get('/api/auth/me').status_code == 200
    assert client('alice').get('/api/auth/me').status_code == 200
    limited = client('alice').get('/api/auth/me')
    assert limited.status_code == 429
    assert limited.headers['retry-after'] == '60'
    assert client('bob').get('/api/auth/me').status_code == 200


def test_parallel_rate_admission_never_exceeds_limit(hosted_app):
    from app.services.rate_limit import admit
    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(lambda _: admit('synthetic', 'parallel', 4), range(20)))
    assert sum(decisions) == 4


def test_hosted_startup_rejects_operator_keys_and_unsafe_configuration(hosted_app, monkeypatch):
    from app.hosted_middleware import validate_hosted_configuration
    for name in ('FIREBASE_PROJECT_ID', 'FIREBASE_WEB_API_KEY', 'FIREBASE_AUTH_DOMAIN', 'FIREBASE_WEB_APP_ID'):
        monkeypatch.setenv(name, 'synthetic')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://synthetic/unused')
    monkeypatch.setenv('APP_ENV', 'staging')
    for name in list(os.environ):
        if name.endswith(('_API_KEY', '_AUTH_TOKEN')) and not name.startswith('FIREBASE_'):
            monkeypatch.delenv(name)
    validate_hosted_configuration()
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-never-share')
    with pytest.raises(RuntimeError, match='provider credentials'): validate_hosted_configuration()
    monkeypatch.delenv('OPENAI_API_KEY')
    monkeypatch.setenv('FIREBASE_AUTH_EMULATOR_HOST', '127.0.0.1:9099')
    with pytest.raises(RuntimeError, match='emulator'): validate_hosted_configuration()
    monkeypatch.delenv('FIREBASE_AUTH_EMULATOR_HOST')
    monkeypatch.setenv('CAE_AUTH_MODE', 'local')
    with pytest.raises(RuntimeError, match='require'): validate_hosted_configuration()
