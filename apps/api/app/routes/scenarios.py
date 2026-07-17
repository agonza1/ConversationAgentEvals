from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from app.schemas.scenarios import ScenarioCreateRequest
from app.services.user_scenario_store import (
    USER_SCENARIOS_SUITE_ID,
    create_user_scenario,
    delete_user_scenario,
    get_user_scenario,
    list_user_scenarios,
)

router = APIRouter(prefix='/api/scenarios', tags=['scenarios'])


@router.get('')
def list_scenarios():
    return {
        'suite_id': USER_SCENARIOS_SUITE_ID,
        'scenarios': list_user_scenarios(),
    }


@router.post('')
def create_scenario(payload: ScenarioCreateRequest):
    try:
        return create_user_scenario(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/{scenario_id}')
def get_scenario(scenario_id: str):
    record = get_user_scenario(scenario_id)
    if record is None:
        raise HTTPException(status_code=404, detail='Scenario not found')
    return record


@router.delete('/{scenario_id}', status_code=204)
def delete_scenario(scenario_id: str):
    if not delete_user_scenario(scenario_id):
        raise HTTPException(status_code=404, detail='User-created scenario not found')
    return Response(status_code=204)
