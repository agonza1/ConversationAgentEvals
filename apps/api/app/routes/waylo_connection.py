"""Local-browser controls for explicitly consented, in-memory Waylo preview login."""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response

from app.services import waylo_connection as connections
from app.services.waylo_login import WayloLoginError, configured_waylo_api_base, login_waylo


router = APIRouter(prefix='/api/waylo/connection', tags=['waylo-connection'])
NO_STORE = {'Cache-Control': 'no-store, private', 'Pragma': 'no-cache', 'Expires': '0'}
MAX_BODY_BYTES = 16 * 1024


def _allowed_local_origins() -> set[str]:
    configured = {item.strip() for item in os.getenv(
        'CAE_WAYLO_ALLOWED_ORIGINS', 'http://127.0.0.1:3012,http://localhost:3012').split(',') if item.strip()}
    try:
        for origin in configured:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
                    or parsed.port is None or parsed.username or parsed.password or parsed.path
                    or parsed.query or parsed.fragment):
                raise ValueError
        if not configured:
            raise ValueError
    except ValueError:
        raise HTTPException(503, 'Waylo connection control must use explicitly ported loopback origins.', headers=NO_STORE) from None
    return configured


def local_control(request: Request) -> None:
    """Require intentional same-origin control, never a forwarded-host trust decision."""
    if request.headers.get('X-CAE-Waylo-Control') != '1':
        raise HTTPException(403, 'Waylo connection control requires a local browser request.', headers=NO_STORE)
    allowed = _allowed_local_origins()
    origin = request.headers.get('origin')
    fetch_site = request.headers.get('sec-fetch-site')
    if fetch_site not in {None, 'same-origin'}:
        raise HTTPException(403, 'Cross-site Waylo connection control is not allowed.', headers=NO_STORE)
    if origin is None:
        if request.method not in {'GET', 'HEAD'} or fetch_site != 'same-origin':
            raise HTTPException(403, 'A trusted local Origin is required.', headers=NO_STORE)
    elif origin not in allowed or origin == 'null':
        raise HTTPException(403, 'A trusted local Origin is required.', headers=NO_STORE)


def session_proof(request: Request) -> str:
    value = request.headers.get('X-CAE-Waylo-Session', '')
    if not value or len(value) > 256:
        raise HTTPException(401, 'Connect to Waylo again to use this local preview session.', headers=NO_STORE)
    return value


def _invalid_input() -> HTTPException:
    # No framework validation error may echo a password-bearing request body.
    return HTTPException(400, 'Invalid Waylo connection request. Check the fields and explicitly confirm sign-in.', headers=NO_STORE)


async def _login_body(request: Request) -> dict:
    content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        raise _invalid_input()
    raw = bytearray()
    try:
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BODY_BYTES:
                raise _invalid_input()
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise _invalid_input() from None
    finally:
        raw.clear()
    expected = {'email', 'password', 'waylo_agent_id', 'tenant_id', 'confirm', 'expected_endpoint_url'}
    if not isinstance(body, dict) or set(body) - expected or body.get('confirm') is not True:
        raise _invalid_input()
    for name, limit in (('email', 320), ('password', 4096), ('expected_endpoint_url', 2048)):
        value = body.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise _invalid_input()
    for name in ('waylo_agent_id', 'tenant_id'):
        value = body.get(name)
        if name == 'tenant_id' and value is None:
            continue
        if not isinstance(value, str):
            raise _invalid_input()
        try:
            body[name] = str(UUID(value))
        except ValueError:
            raise _invalid_input() from None
    return body


@router.get('/config')
def waylo_config(request: Request, response: Response):
    response.headers.update(NO_STORE)
    local_control(request)
    try:
        return {'endpoint_url': configured_waylo_api_base()}
    except WayloLoginError:
        raise HTTPException(503, 'Waylo login is not configured as an allowed HTTPS destination.', headers=NO_STORE) from None


@router.post('/connect')
async def connect_waylo(request: Request, response: Response):
    response.headers.update(NO_STORE)
    local_control(request)
    body = await _login_body(request)
    try:
        if body['expected_endpoint_url'] != configured_waylo_api_base():
            raise HTTPException(400, 'Waylo login destination changed; reload and consent again.', headers=NO_STORE)
        login = await login_waylo(body['email'], body['password'], body['waylo_agent_id'], body.get('tenant_id'),
                                  expected_endpoint_url=body['expected_endpoint_url'])
        proof, status = connections.connect(login.token, login.endpoint_url, login.workspace_id, login.waylo_agent_id)
        previous = request.headers.get('X-CAE-Waylo-Session')
        if previous and previous != proof:
            # Reconnect/account change must cancel the old browser-bound calls,
            # but a failed sign-in must leave the previous grant untouched.
            connections.disconnect(previous)
        return {**status, 'session_proof': proof, 'agent_name': login.agent_name}
    except WayloLoginError as exc:
        raise HTTPException(400, str(exc), headers=NO_STORE) from None
    except ValueError:
        raise HTTPException(400, 'Waylo did not provide a usable, unexpired preview session.', headers=NO_STORE) from None
    finally:
        body.pop('password', None)


@router.get('/status')
def waylo_status(request: Request, response: Response):
    response.headers.update(NO_STORE)
    local_control(request)
    return connections.status(session_proof(request))


@router.post('/disconnect')
def disconnect_waylo(request: Request, response: Response):
    response.headers.update(NO_STORE)
    local_control(request)
    connections.disconnect(session_proof(request))
    return {'connected': False}
