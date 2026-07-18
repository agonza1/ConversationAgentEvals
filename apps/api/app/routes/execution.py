from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.execution import ExecutionRunCreateRequest
from app.services import execution_run_store
from app.services.execution_audio import describe_execution_audio_capabilities
from app.services.execution_runner import execute_execution_run, start_execution_run
from app.services.acc_connection import acc_connection_status, test_acc_connection


router = APIRouter(prefix='/api/execution', tags=['execution'])


class AccConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    base_url: str = Field(min_length=1, max_length=2048)


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
