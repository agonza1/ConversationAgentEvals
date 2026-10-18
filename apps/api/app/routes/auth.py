import os
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from app.security import hosted, user_id, PrivateDocuments
from app.services import firebase_auth
from app.services.user_credentials import openai_key, save_key

router = APIRouter(prefix='/api/auth', tags=['auth'])
COOKIE = '__session'


@router.get('/config')
def config(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return {'enabled': hosted(), 'firebase': {
        'apiKey': os.getenv('FIREBASE_WEB_API_KEY', ''),
        'authDomain': os.getenv('FIREBASE_AUTH_DOMAIN', ''),
        'projectId': os.getenv('FIREBASE_PROJECT_ID', ''),
        'appId': os.getenv('FIREBASE_WEB_APP_ID', ''),
    } if hosted() else None}


class SessionRequest(BaseModel):
    id_token: SecretStr = Field(max_length=16000)


@router.post('/session')
def login(payload: SessionRequest, response: Response):
    if not hosted(): raise HTTPException(404)
    try:
        cookie, claims = firebase_auth.create_session(payload.id_token.get_secret_value())
    except Exception:
        raise HTTPException(401, 'Google sign-in could not be verified. Sign in again.') from None
    response.set_cookie(COOKIE, cookie, httponly=True, secure=os.environ['CAE_PUBLIC_ORIGIN'].startswith('https://'),
                        samesite='lax', max_age=5 * 86400, path='/')
    response.headers['Cache-Control'] = 'no-store'
    return {'uid': claims['sub'], 'email': claims.get('email')}


@router.delete('/session')
def logout(response: Response):
    response.delete_cookie(COOKIE, path='/', secure=os.environ['CAE_PUBLIC_ORIGIN'].startswith('https://'), httponly=True, samesite='lax')
    return {'signed_out': True}


@router.get('/me')
def me(request: Request):
    return {'uid': user_id(), 'email': request.scope.get('cae_claims', {}).get('email')}


class KeyRequest(BaseModel):
    api_key: SecretStr = Field(min_length=12, max_length=512)


@router.get('/openai-key')
def key_status():
    return {'configured': bool(openai_key()), 'billing': 'your_api_key', 'service_fallback': False}


@router.put('/openai-key')
def put_key(payload: KeyRequest):
    value = payload.api_key.get_secret_value().strip()
    if not value.startswith('sk-') or any(char.isspace() for char in value):
        raise HTTPException(422, 'Enter an OpenAI API key beginning with sk-.')
    save_key(value)
    return {'configured': True, 'billing': 'your_api_key', 'service_fallback': False}


@router.delete('/openai-key')
def delete_key():
    records = PrivateDocuments('credentials')
    if 'openai' in records: del records['openai']
    return {'configured': False, 'service_fallback': False}
