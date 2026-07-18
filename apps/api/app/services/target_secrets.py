from __future__ import annotations

import os
import re


HTTP_TARGET_SECRET_ID_PATTERN = r'^[a-z][a-z0-9-]{0,63}$'
_HTTP_TARGET_SECRET_ID_RE = re.compile(HTTP_TARGET_SECRET_ID_PATTERN)
_HTTP_TARGET_SECRET_ENV_PREFIX = 'CAE_HTTP_TARGET_SECRET_'


def http_target_secret_env_name(secret_id: str) -> str:
    """Map a public credential ID into the dedicated HTTP-target secret namespace."""

    normalized = (secret_id or '').strip()
    if not _HTTP_TARGET_SECRET_ID_RE.fullmatch(normalized):
        raise ValueError('HTTP target credential IDs must use lowercase letters, numbers, and hyphens.')
    return f'{_HTTP_TARGET_SECRET_ENV_PREFIX}{normalized.upper().replace("-", "_")}'


def resolve_http_target_secret(secret_id: str) -> str:
    """Resolve only credentials explicitly provisioned for outbound HTTP targets."""

    env_name = http_target_secret_env_name(secret_id)
    secret = (os.getenv(env_name) or '').strip()
    if not secret:
        raise ValueError(f'HTTP target credential is not configured: {secret_id}')
    return secret
