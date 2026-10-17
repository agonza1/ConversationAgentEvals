"""Encrypted BYOK storage and credential resolution with no hosted operator fallback."""
from __future__ import annotations
import os
from cryptography.fernet import Fernet
from app.security import document_get, document_put, hosted, user_id


def cipher() -> Fernet:
    return Fernet(os.environ['CAE_CREDENTIAL_ENCRYPTION_KEY'].encode())


def save_key(value: str) -> None:
    user_id()
    document_put('credentials', 'openai', {'encrypted': cipher().encrypt(value.encode()).decode()})


def openai_key() -> str:
    if hosted():
        record = document_get('credentials', 'openai')
        return cipher().decrypt(record['encrypted'].encode()).decode() if record else ''
    return (os.getenv('OPENAI_API_KEY') or os.getenv('LLM_JUDGE_API_KEY') or '').strip()


def provider_environment() -> dict[str, str]:
    environment = dict(os.environ)
    if hosted():
        # Children receive one user's key, no Firebase/admin/encryption credentials or local tokens.
        environment = {name: value for name, value in environment.items()
                       if name in {'PATH', 'PYTHONPATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
                       or name.startswith(('ASSERT_', 'SPEC_GENERATION_'))}
        environment.update(CAE_AUTH_MODE='local', OPENAI_CODEX_IMPORT_HOME='0', OPENAI_CODEX_OAUTH_PATH='/nonexistent/cae-no-oauth',
                           OPENAI_API_KEY=openai_key())
        from app.services.spec_generation_settings import generation_settings
        environment['SPEC_GENERATION_MODEL'] = generation_settings()['effective_model']
    return environment
