import base64
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from app.services import waylo_connection as wc


ENDPOINT = 'https://api.poc.app.waylovoice.ai'
WORKSPACE = '00000000-0000-4000-8000-000000000001'
AGENT = '00000000-0000-4000-8000-000000000002'
OTHER = '00000000-0000-4000-8000-000000000003'
TARGET = {'target': 'waylo', 'connection': {'endpoint_url': ENDPOINT, 'workspace_id': WORKSPACE, 'waylo_agent_id': AGENT}}


def access_token(exp=1900, **claims):
    body = base64.urlsafe_b64encode(json.dumps({'exp': exp, **claims}).encode()).decode().rstrip('=')
    return 'header.' + body + '.private-signature'


@pytest.fixture
def clock():
    return {'wall': 1000.0, 'mono': 200.0}


@pytest.fixture
def store(clock):
    value = wc.WayloConnectionStore(now=lambda: clock['wall'], monotonic=lambda: clock['mono'], timer_factory=None)
    yield value
    value.clear()


def connect(store, token=None):
    return store.connect(token or access_token(), ENDPOINT, WORKSPACE, AGENT)


def test_connection_status_and_representations_never_expose_token_or_browser_proof(store):
    token = access_token()
    proof, state = connect(store, token)
    lease = store.authorize(proof, TARGET)
    assert state == {'connected': True, 'expires_at': '1970-01-01T00:31:40+00:00', 'remaining_seconds': 900,
                     'endpoint_url': ENDPOINT, 'workspace_id': WORKSPACE, 'waylo_agent_id': AGENT}
    assert lease.token() == token
    assert lease.check() is None
    assert not lease.revoked.is_set()
    text = json.dumps(state) + repr(store) + repr(lease) + repr(store._grants) + repr(store._runs)
    assert token not in text
    assert proof not in text
    assert list(store._grants) == [hashlib.sha256(proof.encode()).hexdigest()]


@pytest.mark.parametrize('proof', [None, '', 'not-a-proof', 'x' * 42, 'x' * 44, '?' * 43, ['secret'], {'secret': 'value'}])
def test_missing_or_malformed_browser_proof_is_not_an_authorization(store, proof):
    connect(store)
    assert store.status(proof) == {'connected': False}
    with pytest.raises(wc.WayloConnectionError, match='Connect Waylo from this browser') as error:
        store.authorize(proof, TARGET)
    if proof:
        assert str(proof) not in str(error.value)
    store.disconnect(proof)


def test_browser_connections_and_disconnect_are_isolated(store):
    first, _ = connect(store)
    second, _ = connect(store, access_token(1800, sub='different-user'))
    first_lease = store.authorize(first, TARGET)
    second_lease = store.authorize(second, TARGET)
    store.bind_run('exec-first', first_lease)
    store.bind_run('exec-second', second_lease)
    store.disconnect(first)
    assert store.status(first) == {'connected': False}
    assert store.status(second)['connected'] is True
    assert first_lease.revoked.is_set()
    assert second_lease.check() is None
    assert store.run_lease('exec-second', TARGET) is second_lease
    with pytest.raises(wc.WayloConnectionError):
        store.run_lease('exec-first', TARGET)
    with pytest.raises(wc.WayloConnectionError):
        first_lease.token()


@pytest.mark.parametrize('field,value', [('endpoint_url', 'https://different-waylo.test'), ('workspace_id', OTHER), ('waylo_agent_id', OTHER)])
def test_authorization_is_bound_to_exact_target_fingerprint(store, field, value):
    proof, _ = connect(store)
    target = deepcopy(TARGET)
    target['connection'][field] = value
    with pytest.raises(wc.WayloConnectionError, match='match the selected target'):
        store.authorize(proof, target)
    lease = store.authorize(proof, TARGET)
    store.bind_run('exec-1', lease)
    with pytest.raises(wc.WayloConnectionError, match='match the selected target'):
        store.run_lease('exec-1', target)


def test_equivalent_url_and_uuid_forms_match_but_target_type_does_not(store):
    proof, _ = store.connect(access_token(), ENDPOINT.upper() + ':443/', WORKSPACE.upper(), AGENT.upper())
    assert store.authorize(proof, TARGET).check() is None
    wrong_type = {**TARGET, 'target': 'http_endpoint'}
    with pytest.raises(wc.WayloConnectionError):
        store.authorize(proof, wrong_type)


@pytest.mark.parametrize('token', ['plain-secret', 'h.not-json.s', 'h.e30.s', '', access_token(True), access_token(float('inf')), access_token('1900'), 'h.' + 'x' * 17000 + '.s'])
def test_expiry_is_required_and_malformed_tokens_do_not_echo_credential(store, token):
    with pytest.raises(wc.WayloConnectionError, match='without a usable expiry') as error:
        store.connect(token, ENDPOINT, WORKSPACE, AGENT)
    if token:
        assert token not in str(error.value)
    assert not store._grants


@pytest.mark.parametrize('expiry', [999, 1000])
def test_expired_token_cannot_be_installed(store, expiry):
    with pytest.raises(wc.WayloConnectionError, match='expired'):
        connect(store, access_token(expiry))
    assert not store._grants


@pytest.mark.parametrize('expiry,seconds', [(1030, 30), (1900, 900), (999999, 900)])
def test_local_grant_lifetime_is_bounded_by_provider_expiry_and_fifteen_minutes(store, expiry, seconds):
    proof, state = connect(store, access_token(expiry))
    assert state['remaining_seconds'] == seconds
    assert store.authorize(proof, TARGET, required_seconds=seconds).check() is None
    with pytest.raises(wc.WayloConnectionError, match='before this call can finish'):
        store.authorize(proof, TARGET, required_seconds=seconds + .1)


def test_existing_lease_rechecks_final_queue_duration_and_elapsed_time(store, clock):
    proof, _ = connect(store)
    lease = store.authorize(proof, TARGET)
    assert lease.check(required_seconds=900) is None
    clock['mono'] += 30
    assert lease.check(required_seconds=870) is None
    with pytest.raises(wc.WayloConnectionError, match='before this call can finish'):
        lease.check(required_seconds=871)
    with pytest.raises(wc.WayloConnectionError, match='valid call duration'):
        lease.check(required_seconds=float('nan'))


@pytest.mark.parametrize('duration', [-1, float('inf'), float('nan'), True, '30'])
def test_call_budget_must_be_finite_and_nonnegative(store, duration):
    proof, _ = connect(store)
    with pytest.raises(wc.WayloConnectionError, match='valid call duration'):
        store.authorize(proof, TARGET, required_seconds=duration)


@pytest.mark.parametrize('wall,mono', [(1900, 200), (1000, 1100), (0, 1100)])
def test_expiry_invalidates_leases_and_runs_even_when_other_clock_goes_backwards(store, clock, wall, mono):
    proof, _ = connect(store)
    lease = store.authorize(proof, TARGET)
    store.bind_run('exec-expired', lease)
    clock.update(wall=wall, mono=mono)
    with pytest.raises(wc.WayloConnectionError, match='ended'):
        lease.check()
    assert lease.revoked.is_set()
    assert store.status(proof) == {'connected': False}
    assert not store._runs
    assert not store._grants


def test_expiry_timer_signals_event_without_another_request(clock):
    timers = []
    class FakeTimer:
        def __init__(self, seconds, callback, args):
            self.seconds, self.callback, self.args = seconds, callback, args
            self.started = self.cancelled = False
            timers.append(self)
        def start(self): self.started = True
        def cancel(self): self.cancelled = True
    store = wc.WayloConnectionStore(now=lambda: clock['wall'], monotonic=lambda: clock['mono'], timer_factory=FakeTimer)
    proof, _ = connect(store)
    lease = store.authorize(proof, TARGET)
    assert timers[0].seconds == 900 and timers[0].started and timers[0].daemon
    timers[0].callback(*timers[0].args)
    assert lease.revoked.is_set()
    assert timers[0].cancelled
    with pytest.raises(wc.WayloConnectionError):
        lease.token()


def test_restart_reset_revokes_existing_lease_and_does_not_restore_browser_access(store):
    proof, _ = connect(store)
    lease = store.authorize(proof, TARGET)
    store.bind_run('exec-restart', lease)
    store.clear()
    restarted = wc.WayloConnectionStore(timer_factory=None)
    assert restarted.status(proof) == {'connected': False}
    assert lease.revoked.is_set()
    assert not store._runs
    with pytest.raises(wc.WayloConnectionError):
        lease.check()


def test_upstream_rejection_revokes_all_runs_for_that_grant(store):
    proof, _ = connect(store)
    first = store.authorize(proof, TARGET)
    second = store.authorize(proof, TARGET)
    store.bind_run('exec-1', first)
    store.bind_run('exec-2', second)
    first.revoke()
    assert first.revoked.is_set() and second.revoked.is_set()
    assert store.status(proof) == {'connected': False}
    assert not store._runs
    with pytest.raises(wc.WayloConnectionError):
        second.token()


def test_release_run_drops_binding_without_disconnecting_account(store):
    proof, _ = connect(store)
    lease = store.authorize(proof, TARGET)
    store.bind_run('exec-release', lease)
    store.release_run('exec-release')
    store.release_run('unknown')
    assert lease.check() is None
    assert store.status(proof)['connected']
    with pytest.raises(wc.WayloConnectionError):
        store.run_lease('exec-release', TARGET)


def test_duplicate_run_binding_cannot_overwrite_a_different_browsers_grant(store):
    first, _ = connect(store)
    second, _ = connect(store)
    owner = store.authorize(first, TARGET)
    attacker = store.authorize(second, TARGET)
    store.bind_run('exec-shared', owner)
    with pytest.raises(wc.WayloConnectionError, match='already'):
        store.bind_run('exec-shared', attacker)
    assert store.run_lease('exec-shared', TARGET) is owner


def test_binding_after_disconnect_rejects_queue_race(store):
    proof, _ = connect(store)
    lease = store.authorize(proof, TARGET)
    store.disconnect(proof)
    with pytest.raises(wc.WayloConnectionError):
        store.bind_run('exec-race', lease)
    assert not store._runs


def test_lease_from_another_store_cannot_be_bound(store):
    proof, _ = connect(store)
    lease = store.authorize(proof, TARGET)
    other = wc.WayloConnectionStore(timer_factory=None)
    with pytest.raises(wc.WayloConnectionError):
        other.bind_run('exec-other', lease)


def test_memory_capacity_is_bounded_and_expired_grants_are_reclaimed(clock):
    store = wc.WayloConnectionStore(now=lambda: clock['wall'], monotonic=lambda: clock['mono'],
                                    timer_factory=None, capacity=1, run_capacity=1)
    proof, _ = connect(store, access_token(1010))
    lease = store.authorize(proof, TARGET)
    store.bind_run('exec-1', lease)
    with pytest.raises(wc.WayloConnectionError, match='connection capacity'):
        connect(store)
    with pytest.raises(wc.WayloConnectionError, match='run capacity'):
        store.bind_run('exec-2', lease)
    clock['mono'] += 10
    new_proof, _ = connect(store)
    assert lease.revoked.is_set()
    assert not store._runs
    assert store.status(new_proof)['connected']


def test_concurrent_run_binding_has_one_owner(store):
    proof, _ = connect(store)
    lease = store.authorize(proof, TARGET)
    barrier = threading.Barrier(8)
    def bind():
        barrier.wait()
        try:
            store.bind_run('exec-race', lease)
            return 'bound'
        except wc.WayloConnectionError:
            return 'rejected'
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: bind(), range(8)))
    assert outcomes.count('bound') == 1
    assert outcomes.count('rejected') == 7


@pytest.mark.parametrize('endpoint', ['http://remote.test', 'https://user:secret@api.test', 'https://api.test?secret=value', 'https://api.test#secret', 'file:///tmp/file', 'https://api.test:broken'])
def test_unsafe_endpoint_is_rejected_without_echoing_values(store, endpoint):
    with pytest.raises(wc.WayloConnectionError) as error:
        store.connect(access_token(), endpoint, WORKSPACE, AGENT)
    assert endpoint not in str(error.value)


def test_module_wrappers_share_only_the_in_memory_store(store, monkeypatch):
    monkeypatch.setattr(wc, 'connection_store', store)
    proof, _ = wc.connect(access_token(), ENDPOINT, WORKSPACE, AGENT)
    lease = wc.authorize(proof, TARGET)
    assert wc.authorize_lease_target(lease, TARGET) is None
    wrong_target = deepcopy(TARGET)
    wrong_target['connection']['waylo_agent_id'] = OTHER
    with pytest.raises(wc.WayloConnectionError, match='match the selected target'):
        wc.authorize_lease_target(lease, wrong_target)
    wc.bind_run('exec-wrappers', lease)
    assert wc.run_lease('exec-wrappers', TARGET) is lease
    assert wc.status(proof)['connected']
    wc.release_run('exec-wrappers')
    wc.disconnect(proof)
    assert wc.status(proof) == {'connected': False}
