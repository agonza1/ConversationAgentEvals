"""Explicit, local-preview Waylo login; passwords and refresh cookies are never stored."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID

import httpx


DEFAULT_WAYLO_API_BASE = 'https://api.poc.app.waylovoice.ai'
MAX_RESPONSE_BYTES = 128 * 1024


class WayloLoginError(ValueError):
    """A deliberately static error, safe to expose without provider response data."""


@dataclass(frozen=True)
class WayloLogin:
    token: str = field(repr=False)
    endpoint_url: str
    workspace_id: str
    waylo_agent_id: str
    agent_name: str


def configured_waylo_api_base() -> str:
    """Only an operator-selected, explicitly allowlisted HTTPS origin may receive passwords."""
    base = os.getenv('CAE_WAYLO_API_BASE_URL', DEFAULT_WAYLO_API_BASE).rstrip('/')
    allowed = {item.strip().rstrip('/') for item in os.getenv(
        'CAE_WAYLO_ALLOWED_API_ORIGINS', DEFAULT_WAYLO_API_BASE).split(',') if item.strip()}
    try:
        parsed = urlsplit(base)
        parsed.port  # Validate a configured port without forwarding credentials first.
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or base not in allowed):
            raise ValueError
    except ValueError:
        raise WayloLoginError('The Waylo login API is not configured as an allowed HTTPS origin.') from None
    return base


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise WayloLoginError('Waylo returned an invalid agent or workspace identifier.')
    try:
        return str(UUID(value))
    except ValueError:
        raise WayloLoginError('Waylo returned an invalid agent or workspace identifier.') from None


async def login_waylo(email: str, password: str, waylo_agent_id: str,
                     tenant_id: str | None = None, *, client: httpx.AsyncClient | None = None,
                     expected_endpoint_url: str | None = None) -> WayloLogin:
    """Exchange one consented password submission for an access JWT and verify its target.

    A refresh cookie belongs to the first-party browser, not this connector. Drop all
    response cookies immediately and never refresh or follow provider redirects.
    """
    base = configured_waylo_api_base()
    if expected_endpoint_url is not None and expected_endpoint_url != base:
        raise WayloLoginError('Waylo login destination changed; reload and consent again.')
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False)

    async def read_json(method: str, path: str, **kwargs) -> dict:
        try:
            response = await client.request(method, f'{base}/{path}', follow_redirects=False, **kwargs)
        except httpx.HTTPError:
            raise WayloLoginError('Waylo could not be reached securely. Please try again.') from None
        finally:
            client.cookies.clear()
        if response.status_code in {401, 403}:
            raise WayloLoginError('Waylo sign-in or access was refused. Check your account and workspace access.')
        if response.status_code == 429:
            raise WayloLoginError('Waylo sign-in is temporarily rate limited. Please try later.')
        if not 200 <= response.status_code < 300 or len(response.content) > MAX_RESPONSE_BYTES:
            raise WayloLoginError('Waylo returned an unsupported login response.')
        try:
            value = response.json()
        except ValueError:
            raise WayloLoginError('Waylo returned an unsupported login response.') from None
        if not isinstance(value, dict):
            raise WayloLoginError('Waylo returned an unsupported login response.')
        return value

    try:
        client.cookies.clear()
        signed_in = await read_json('POST', 'auth/login', json={'email': email, 'password': password})
        token = signed_in.get('token')
        if not isinstance(token, str) or not 32 <= len(token) <= 16384:
            raise WayloLoginError('Waylo returned an unsupported access token.')
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
        principal = await read_json('GET', 'auth/me', headers=headers)
        if type(principal.get('isPlatformAdmin')) is not bool:
            raise WayloLoginError('Waylo did not confirm active account authority.')
        if principal.get('isPlatformAdmin') is True:
            if tenant_id is None:
                raise WayloLoginError('A Waylo platform-admin account must explicitly select a Waylo tenant UUID.')
            endpoint = f'{base}/admin/tenants/{_uuid(tenant_id)}'
            agent_path = f'admin/tenants/{_uuid(tenant_id)}/agents/{_uuid(waylo_agent_id)}'
        else:
            if tenant_id is not None:
                raise WayloLoginError('Tenant selection is available only to Waylo platform-admin accounts.')
            _uuid(principal.get('tenantId'))
            # Upstream tenant_members.role_key's exact check constraint, not a
            # workspace role or an authority inferred from JWT payload claims.
            if principal.get('tenantRole') not in {'owner', 'admin', 'member'}:
                raise WayloLoginError('Waylo did not confirm active tenant authority.')
            endpoint = base
            agent_path = f'agents/{_uuid(waylo_agent_id)}'
        agent = await read_json('GET', agent_path, headers=headers)
        agent_id = _uuid(agent.get('id'))
        workspace_id = _uuid(agent.get('workspaceId'))
        if agent_id != _uuid(waylo_agent_id):
            raise WayloLoginError('Waylo returned a different agent than the one selected.')
        # Only provider-owned metadata, never account names, auth bodies or credentials.
        name = agent.get('name')
        if (not isinstance(name, str) or not name.strip() or len(name) > 120
                or token in name or password in name or 'bearer ' in name.lower() or '://' in name):
            name = 'Waylo agent'
        return WayloLogin(token, endpoint, workspace_id, agent_id, name)
    finally:
        client.cookies.clear()
        if owns_client:
            await client.aclose()
