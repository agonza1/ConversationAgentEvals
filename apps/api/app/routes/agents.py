from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.agents import AgentCreateRequest, AgentUpdateRequest
from app.services import agent_store


router = APIRouter(prefix='/api/agents', tags=['agents'])


@router.get('')
def list_agents():
    return {'agents': agent_store.list_agents()}


@router.post('')
def create_agent(payload: AgentCreateRequest):
    try:
        return agent_store.create_agent(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/{agent_id}')
def get_agent(agent_id: str):
    agent = agent_store.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail='Agent not found.')
    return agent


@router.patch('/{agent_id}')
def update_agent(agent_id: str, payload: AgentUpdateRequest):
    try:
        agent = agent_store.update_agent(agent_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if agent is None:
        raise HTTPException(status_code=404, detail='Agent not found.')
    return agent


@router.delete('/{agent_id}')
def delete_agent(agent_id: str):
    try:
        deleted = agent_store.delete_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail='Agent not found.')
    return {'ok': True, 'id': agent_id}
