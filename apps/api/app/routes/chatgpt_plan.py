"""Deployment-local control plane, not hosted user authentication."""
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.services.llm_providers.chatgpt_plan import ChatGPTPlanError, get_chatgpt_provider

router = APIRouter(prefix='/api/product/providers/chatgpt', tags=['chatgpt-plan'])


def local_control(request: Request) -> None:
    # Next's same-origin rewrite forwards the browser host through this header.
    host = request.headers.get('x-forwarded-host') or request.headers.get('host', '')
    if urlsplit('http://' + host).hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise HTTPException(status_code=403, detail='ChatGPT account controls require a loopback local CAE host.')
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise HTTPException(status_code=403, detail='Cross-site ChatGPT account control is not allowed.')
    if request.method != 'GET' and request.headers.get('x-cae-local-control') != '1':
        raise HTTPException(status_code=403, detail='Use the local CAE Console Settings controls.')


class ProfileSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)


class JudgeModelSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str | None = Field(default=None, min_length=1, max_length=160)


def call(action):
    try:
        return action()
    except ChatGPTPlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get('/status', dependencies=[Depends(local_control)])
def status():
    return call(lambda: get_chatgpt_provider().status())


@router.post('/oauth/start', dependencies=[Depends(local_control)])
def connect(selection: ProfileSelection):
    return call(lambda: get_chatgpt_provider().start_oauth(selection.profile_id))


@router.get('/models', dependencies=[Depends(local_control)])
def models():
    return call(lambda: {'models': get_chatgpt_provider().list_models()})


@router.post('/account', dependencies=[Depends(local_control)])
def select_account(selection: ProfileSelection):
    if not selection.profile_id:
        raise HTTPException(status_code=422, detail='Choose a saved account.')
    return call(lambda: get_chatgpt_provider().select_profile(selection.profile_id))


@router.post('/judge-model', dependencies=[Depends(local_control)])
def select_model(selection: JudgeModelSelection):
    return call(lambda: get_chatgpt_provider().select_judge_model(selection.model))


@router.post('/disconnect', dependencies=[Depends(local_control)])
def disconnect():
    return call(lambda: get_chatgpt_provider().disconnect())
