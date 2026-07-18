from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.execution import ExecutionRunCreateRequest
from app.services import execution_run_store
from app.services.execution_audio import describe_execution_audio_capabilities
from app.services.execution_runner import execute_execution_run, start_execution_run
from app.services.reference_generalist_agent import (
    ReferenceRuntimeError,
    resolve_reference_completion_provider,
)
from app.services.acc_connection import acc_connection_status, test_acc_connection


router = APIRouter(prefix='/api/execution', tags=['execution'])


class AccConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    base_url: str = Field(min_length=1, max_length=2048)


class ReferenceCompletionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    prompt: str = Field(min_length=1, max_length=50000)
    model_name: str = Field(min_length=1, max_length=128)


@router.post('/reference/complete')
def execution_reference_complete(
    payload: ReferenceCompletionRequest,
    x_cae_reference_token: str | None = Header(default=None),
):
    """Local Pipecat target callback using configured API-key or Codex OAuth auth.

    Credentials never leave the API process; the Pipecat participant receives only
    the prompt and response text on the local service boundary.
    """
    expected_token = os.getenv('REFERENCE_AGENT_INTERNAL_TOKEN', '').strip()
    if not expected_token:
        raise HTTPException(status_code=503, detail='Set REFERENCE_AGENT_INTERNAL_TOKEN for the local reference pipeline.')
    if not x_cae_reference_token or not secrets.compare_digest(x_cae_reference_token, expected_token):
        raise HTTPException(status_code=403, detail='Invalid local reference-agent token.')
    try:
        provider = resolve_reference_completion_provider()
        text = provider.complete(payload.prompt, model_name=payload.model_name).strip()
    except (ReferenceRuntimeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not text:
        raise HTTPException(status_code=502, detail='Reference LLM returned empty response text.')
    status = provider.status()
    return {
        'text': text,
        'provider': status.get('provider') or provider.provider_id,
        'model_name': payload.model_name,
    }


@router.post('/runs')
def create_execution_run(payload: ExecutionRunCreateRequest, background_tasks: BackgroundTasks):
    try:
        queued = start_execution_run(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(execute_execution_run, queued['execution_run_id'], payload)
    return queued


@router.get('/runs')
def list_runs(
    user_id: str = Query(...),
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    return execution_run_store.list_execution_runs(
        user_id=user_id,
        project_id=project_id,
        status=status,
    )


@router.get('/runs/{execution_run_id}')
def get_run(execution_run_id: str, user_id: str = Query(...)):
    run = execution_run_store.get_execution_run(execution_run_id)
    if run is None or run.get('user_id') != user_id:
        raise HTTPException(status_code=404, detail='Execution run not found.')
    return run


@router.get('/runs/{execution_run_id}/conversations/{conversation_id}')
def get_conversation(execution_run_id: str, conversation_id: str, user_id: str = Query(...)):
    run = execution_run_store.get_execution_run(execution_run_id)
    if run is None or run.get('user_id') != user_id:
        raise HTTPException(status_code=404, detail='Execution run not found.')
    conversation = execution_run_store.get_conversation(execution_run_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail='Conversation not found.')
    return conversation


@router.get('/runs/{execution_run_id}/conversations/{conversation_id}/audio/{sequence}')
def get_live_conversation_audio(
    execution_run_id: str,
    conversation_id: str,
    sequence: int,
    user_id: str = Query(...),
):
    run = execution_run_store.get_execution_run(execution_run_id)
    if run is None or run.get('user_id') != user_id:
        raise HTTPException(status_code=404, detail='Execution run not found.')
    conversation = execution_run_store.get_conversation(execution_run_id, conversation_id)
    matching = next(
        (
            event for event in (conversation or {}).get('live_events') or []
            if event.get('sequence') == sequence and event.get('kind') == 'audio'
        ),
        None,
    )
    if matching is None:
        raise HTTPException(status_code=404, detail='Live audio segment not found.')
    root = (execution_run_store.RUNS_DIR / execution_run_id).resolve()
    path = (
        root
        / 'audio'
        / 'live'
        / f'{conversation_id}-{sequence}.wav'
    ).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail='Live audio segment not found.')
    return FileResponse(path, media_type='audio/wav')


@router.get('/audio/capabilities')
def execution_audio_capabilities():
    """Describe available execution-time audio transports and vCon capture.

    Default CI uses the built-in local audio loop. FreeSWITCH Verto / ACC SIP
    outbound dialing is advertised as deferred and is not required to install CAE.
    """

    return describe_execution_audio_capabilities().model_dump(mode='json')


@router.get('/acc-connection')
def execution_acc_connection(base_url: str | None = Query(default=None)):
    """Readiness for ACC-owned live destinations (SIP / phone / browser WebRTC)."""

    return acc_connection_status(base_url=base_url)


@router.post('/acc-connection/test')
def execution_test_acc_connection(payload: AccConnectionTestRequest):
    """Probe ACC's official media-readiness route and expose adapter capability."""

    try:
        return test_acc_connection(payload.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/health')
def execution_health(db: Session = Depends(get_db)):
    del db
    return {
        'ok': True,
        'surface': 'execution',
        'audio': describe_execution_audio_capabilities().model_dump(mode='json'),
        'acc_connection': acc_connection_status(),
    }
