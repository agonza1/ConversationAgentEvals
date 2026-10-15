"""Browser-bound, process-memory-only grants for a local Waylo connection.

This module does not authenticate a token. ``connect`` is called only after a
trusted provider has authenticated the person and validated the access token.
The JWT payload is inspected solely to shorten the local lifetime. Neither the
provider token nor the browser's proof belongs in an agent or run snapshot.

There is intentionally no environment, disk, refresh-cookie or network fallback.
An API restart loses every grant. Deploy this local control with one API worker.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID


_PROOF = re.compile(r'^[A-Za-z0-9_-]{43}$')
MAX_LOCAL_SECONDS = 15 * 60


class WayloConnectionError(ValueError):
    """A boundary error whose text never includes a credential or submitted value."""


@dataclass(frozen=True)
class TargetFingerprint:
    endpoint: str
    workspace_id: str
    agent_id: str


def _fingerprint(endpoint: str, workspace_id: str, agent_id: str) -> TargetFingerprint:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        host = parsed.hostname
        if (
            parsed.scheme not in {'http', 'https'} or not host
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme == 'http' and host not in {'localhost', '127.0.0.1', '::1', 'host.docker.internal'})
        ):
            raise ValueError
        netloc = f'[{host}]' if ':' in host else host
        if port is not None and port != (443 if parsed.scheme == 'https' else 80):
            netloc += f':{port}'
        normalized = urlunsplit((parsed.scheme, netloc, parsed.path.rstrip('/'), '', ''))
        return TargetFingerprint(normalized, str(UUID(workspace_id)), str(UUID(agent_id)))
    except (AttributeError, TypeError, ValueError):
        raise WayloConnectionError('Waylo connection requires a safe API URL and workspace/agent UUIDs.') from None


def _target_fingerprint(target: dict) -> TargetFingerprint:
    try:
        if target.get('target') != 'waylo':
            raise ValueError
        connection = target['connection']
        return _fingerprint(connection['endpoint_url'], connection['workspace_id'], connection['waylo_agent_id'])
    except (AttributeError, KeyError, TypeError, ValueError):
        raise WayloConnectionError('This Waylo connection does not match the selected target.') from None


def _proof_key(proof: str | None) -> str:
    if not isinstance(proof, str) or not _PROOF.fullmatch(proof):
        raise WayloConnectionError('Connect Waylo from this browser before continuing.')
    return hashlib.sha256(proof.encode('ascii')).hexdigest()


def _token_expiry(token: str) -> float:
    # Decoding is NOT token verification or an authorization decision. The
    # caller's trusted-provider validation is the prerequisite for installation.
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 16_384:
            raise ValueError
        parts = token.split('.')
        if len(parts) != 3 or not all(parts):
            raise ValueError
        body = parts[1]
        claims = json.loads(base64.b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True))
        expiry = claims['exp']
        if type(expiry) not in {int, float} or not math.isfinite(expiry):
            raise ValueError
        return float(expiry)
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError):
        raise WayloConnectionError('Waylo returned an access token without a usable expiry.') from None


@dataclass(repr=False)
class _Grant:
    token: str = field(repr=False)
    fingerprint: TargetFingerprint
    deadline: float
    expires_at: float
    revoked: threading.Event = field(default_factory=threading.Event, repr=False)
    timer: threading.Timer | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return '<WayloMemoryGrant>'


class Lease:
    """A revocable authorization reference, not a copy of the access token."""

    __slots__ = ('_store', '_key', '_revoked')

    def __init__(self, store: WayloConnectionStore, key: str, revoked: threading.Event):
        self._store = store
        self._key = key
        self._revoked = revoked

    def __repr__(self) -> str:
        return '<WayloConnectionLease>'

    @property
    def revoked(self) -> threading.Event:
        return self._revoked

    def check(self, required_seconds: float = 0) -> None:
        self._store._lease_token(self._key, self._revoked, required_seconds=required_seconds)

    def token(self) -> str:
        return self._store._lease_token(self._key, self._revoked)

    def revoke(self) -> None:
        """Invalidate this grant and every bound run after upstream rejection."""
        self._store._revoke_lease(self._key, self._revoked)


class WayloConnectionStore:
    def __init__(
        self,
        *,
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        capacity: int = 32,
        run_capacity: int = 128,
        timer_factory: Callable[..., threading.Timer] | None = threading.Timer,
    ):
        if capacity < 1 or run_capacity < 1:
            raise ValueError('Waylo memory-store capacities must be positive.')
        self._now = now
        self._monotonic = monotonic
        self._capacity = capacity
        self._run_capacity = run_capacity
        self._timer_factory = timer_factory
        self._lock = threading.Lock()
        self._grants: dict[str, _Grant] = {}
        self._runs: dict[str, Lease] = {}

    def __repr__(self) -> str:
        return '<WayloConnectionStore>'

    def connect(self, token: str, endpoint: str, workspace_id: str, agent_id: str) -> tuple[str, dict]:
        fingerprint = _fingerprint(endpoint, workspace_id, agent_id)
        expires_at = _token_expiry(token)
        with self._lock:
            self._sweep_unlocked()
            wall_now = self._now()
            seconds = min(expires_at - wall_now, MAX_LOCAL_SECONDS)
            if seconds <= 0:
                raise WayloConnectionError('Waylo access has expired; connect again.')
            if len(self._grants) >= self._capacity:
                raise WayloConnectionError('Waylo local connection capacity reached; disconnect an unused connection.')
            # Keep only a hash of the browser capability, never its plaintext.
            while True:
                proof = secrets.token_urlsafe(32)
                key = _proof_key(proof)
                if key not in self._grants:
                    break
            grant = _Grant(token=token, fingerprint=fingerprint, deadline=self._monotonic() + seconds,
                           expires_at=wall_now + seconds)
            self._grants[key] = grant
            if self._timer_factory is not None:
                grant.timer = self._timer_factory(seconds, self._expire, args=(key,))
                grant.timer.daemon = True
                grant.timer.start()
            return proof, self._status_unlocked(grant)

    def status(self, proof: str | None) -> dict:
        try:
            key = _proof_key(proof)
        except WayloConnectionError:
            return {'connected': False}
        with self._lock:
            self._sweep_unlocked()
            grant = self._grants.get(key)
            return self._status_unlocked(grant) if grant else {'connected': False}

    def authorize(self, proof: str | None, target: dict, required_seconds: float = 0) -> Lease:
        key = _proof_key(proof)
        fingerprint = _target_fingerprint(target)
        if type(required_seconds) not in {int, float} or not math.isfinite(required_seconds) or required_seconds < 0:
            raise WayloConnectionError('Waylo authorization requires a valid call duration.')
        with self._lock:
            self._sweep_unlocked()
            grant = self._grant_unlocked(key)
            if grant.fingerprint != fingerprint:
                raise WayloConnectionError('This Waylo connection does not match the selected target.')
            if self._remaining_unlocked(grant) < required_seconds:
                raise WayloConnectionError('Waylo access expires before this call can finish; connect again or shorten the run.')
            return Lease(self, key, grant.revoked)

    def authorize_lease_target(self, lease: Lease, target: dict) -> None:
        fingerprint = _target_fingerprint(target)
        if not isinstance(lease, Lease) or lease._store is not self:
            raise WayloConnectionError('Waylo run authorization is unavailable; connect again.')
        with self._lock:
            self._sweep_unlocked()
            grant = self._grant_unlocked(lease._key)
            if grant.revoked is not lease._revoked or grant.fingerprint != fingerprint:
                raise WayloConnectionError('This Waylo connection does not match the selected target.')

    def disconnect(self, proof: str | None) -> None:
        # Idempotent without providing a grant-existence oracle or touching any
        # other browser's authorization.
        try:
            key = _proof_key(proof)
        except WayloConnectionError:
            return
        with self._lock:
            self._revoke_unlocked(key)

    def bind_run(self, run_id: str, lease: Lease) -> None:
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
            raise WayloConnectionError('Waylo run authorization requires a valid run ID.')
        if not isinstance(lease, Lease) or lease._store is not self:
            raise WayloConnectionError('Waylo run authorization is unavailable; connect again.')
        with self._lock:
            self._sweep_unlocked()
            grant = self._grant_unlocked(lease._key)
            if grant.revoked is not lease._revoked:
                raise WayloConnectionError('Waylo run authorization is unavailable; connect again.')
            if run_id in self._runs:
                raise WayloConnectionError('Waylo run already has an authorization binding.')
            if len(self._runs) >= self._run_capacity:
                raise WayloConnectionError('Waylo local run capacity reached; wait for a running call to finish.')
            self._runs[run_id] = lease

    def run_lease(self, run_id: str, target: dict) -> Lease:
        fingerprint = _target_fingerprint(target)
        with self._lock:
            self._sweep_unlocked()
            lease = self._runs.get(run_id)
            if lease is None:
                raise WayloConnectionError('Waylo run authorization is unavailable; connect again.')
            grant = self._grant_unlocked(lease._key)
            if grant.revoked is not lease._revoked or grant.fingerprint != fingerprint:
                raise WayloConnectionError('This Waylo connection does not match the selected target.')
            return lease

    def release_run(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

    def clear(self) -> None:
        """Shutdown/test reset; not an externally exposed account control."""
        with self._lock:
            for key in list(self._grants):
                self._revoke_unlocked(key)
            self._runs.clear()

    def _revoke_lease(self, key: str, revoked: threading.Event) -> None:
        with self._lock:
            grant = self._grants.get(key)
            if grant is not None and grant.revoked is revoked:
                self._revoke_unlocked(key)
            revoked.set()

    def _lease_token(self, key: str, revoked: threading.Event, *, required_seconds: float = 0) -> str:
        if type(required_seconds) not in {int, float} or not math.isfinite(required_seconds) or required_seconds < 0:
            raise WayloConnectionError('Waylo authorization requires a valid call duration.')
        with self._lock:
            self._sweep_unlocked()
            grant = self._grant_unlocked(key)
            if grant.revoked is not revoked:
                raise WayloConnectionError('Waylo connection has ended; connect again.')
            if self._remaining_unlocked(grant) < required_seconds:
                raise WayloConnectionError('Waylo access expires before this call can finish; connect again or shorten the run.')
            return grant.token

    def _grant_unlocked(self, key: str) -> _Grant:
        grant = self._grants.get(key)
        if grant is None or grant.revoked.is_set():
            raise WayloConnectionError('Waylo connection has ended; connect again.')
        return grant

    def _remaining_unlocked(self, grant: _Grant) -> float:
        # Neither a backwards wall-clock adjustment nor a forwards monotonic
        # discontinuity is allowed to extend an access token's lifetime.
        return min(grant.deadline - self._monotonic(), grant.expires_at - self._now())

    def _status_unlocked(self, grant: _Grant) -> dict:
        return {'connected': True, 'expires_at': datetime.fromtimestamp(grant.expires_at, UTC).isoformat(),
                'remaining_seconds': max(0, math.floor(self._remaining_unlocked(grant))),
                'endpoint_url': grant.fingerprint.endpoint, 'workspace_id': grant.fingerprint.workspace_id,
                'waylo_agent_id': grant.fingerprint.agent_id}

    def _sweep_unlocked(self) -> None:
        for key, grant in list(self._grants.items()):
            if self._remaining_unlocked(grant) <= 0:
                self._revoke_unlocked(key)

    def _expire(self, key: str) -> None:
        with self._lock:
            self._revoke_unlocked(key)

    def _revoke_unlocked(self, key: str) -> None:
        grant = self._grants.pop(key, None)
        if grant is not None:
            grant.revoked.set()
            grant.token = ''
            if grant.timer is not None:
                grant.timer.cancel()
        for run_id, lease in list(self._runs.items()):
            if lease._key == key:
                self._runs.pop(run_id, None)


connection_store = WayloConnectionStore()


def connect(token: str, endpoint: str, workspace_id: str, agent_id: str) -> tuple[str, dict]:
    return connection_store.connect(token, endpoint, workspace_id, agent_id)


def status(proof: str | None) -> dict:
    return connection_store.status(proof)


def authorize(proof: str | None, target: dict, required_seconds: float = 0) -> Lease:
    return connection_store.authorize(proof, target, required_seconds)


def authorize_lease_target(lease: Lease, target: dict) -> None:
    connection_store.authorize_lease_target(lease, target)


def disconnect(proof: str | None) -> None:
    connection_store.disconnect(proof)


def bind_run(run_id: str, lease: Lease) -> None:
    connection_store.bind_run(run_id, lease)


def run_lease(run_id: str, target: dict) -> Lease:
    return connection_store.run_lease(run_id, target)


def release_run(run_id: str) -> None:
    connection_store.release_run(run_id)
