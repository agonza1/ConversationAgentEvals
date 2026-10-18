"""Firebase session verification. The emulator is deliberately unsupported on hosted servers."""
import os
from functools import lru_cache


@lru_cache
def firebase_app():
    import firebase_admin
    return firebase_admin.initialize_app(options={'projectId': os.environ['FIREBASE_PROJECT_ID']}, name='cae-auth')


def verify_session(cookie: str) -> dict:
    from firebase_admin import auth
    return auth.verify_session_cookie(cookie, check_revoked=True, app=firebase_app())


def create_session(token: str) -> tuple[str, dict]:
    import time
    from datetime import timedelta
    from firebase_admin import auth
    claims = auth.verify_id_token(token, check_revoked=True, app=firebase_app())
    if claims.get('firebase', {}).get('sign_in_provider') != 'google.com' or not claims.get('email_verified'):
        raise ValueError('Use a verified Google account.')
    if time.time() - claims.get('auth_time', 0) > 300:
        raise ValueError('Sign in again to start a session.')
    cookie = auth.create_session_cookie(token, expires_in=timedelta(days=5), app=firebase_app())
    return cookie, claims
