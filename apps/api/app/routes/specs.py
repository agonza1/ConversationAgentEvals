from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.services.editable_assert_spec import (
    EditableAssertSpec,
    default_templates,
    export_saved_spec,
    generate_spec_draft,
    get_spec,
    preview_spec,
    save_spec,
    validate_spec,
)


router = APIRouter(prefix='/api/specs', tags=['specs'])


class SpecDraftGenerateRequest(BaseModel):
    title: str = ''
    role: str = ''
    objective: str = ''


class SpecEnvelope(BaseModel):
    spec: EditableAssertSpec


class SpecSaveRequest(BaseModel):
    user_id: str = Field(min_length=1)
    project_id: str = Field(default='default', min_length=1)
    spec: EditableAssertSpec


@router.get('/templates')
def list_spec_templates():
    return {'templates': default_templates()}


@router.post('/generate')
def generate_editable_spec_draft(payload: SpecDraftGenerateRequest):
    return generate_spec_draft(title=payload.title, role=payload.role, objective=payload.objective)


@router.post('/validate')
def validate_editable_spec(payload: SpecEnvelope):
    return validate_spec(payload.spec)


@router.post('/preview')
def preview_editable_spec(payload: SpecEnvelope):
    return preview_spec(payload.spec)


@router.post('')
def create_editable_spec(payload: SpecSaveRequest):
    try:
        return save_spec(user_id=payload.user_id, project_id=payload.project_id, spec=payload.spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/{spec_id}')
def get_editable_spec(spec_id: str, user_id: str = Query(min_length=1), project_id: str = Query(default='default', min_length=1)):
    saved = get_spec(spec_id, user_id=user_id, project_id=project_id)
    if saved is None:
        raise HTTPException(status_code=404, detail='Spec not found')
    return saved


@router.patch('/{spec_id}')
def update_editable_spec(spec_id: str, payload: SpecSaveRequest):
    try:
        return save_spec(user_id=payload.user_id, project_id=payload.project_id, spec=payload.spec.model_copy(update={'id': spec_id}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/{spec_id}/versions')
def create_editable_spec_version(spec_id: str, payload: SpecSaveRequest):
    try:
        return save_spec(user_id=payload.user_id, project_id=payload.project_id, spec=payload.spec.model_copy(update={'id': spec_id}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/{spec_id}/export')
def export_editable_spec(
    spec_id: str,
    user_id: str = Query(min_length=1),
    project_id: str = Query(default='default', min_length=1),
    format: Literal['json', 'yaml'] = Query(default='yaml'),
):
    exported = export_saved_spec(spec_id, user_id=user_id, project_id=project_id, format=format)
    if exported is None:
        raise HTTPException(status_code=404, detail='Spec not found')
    if format == 'yaml':
        return Response(content=str(exported), media_type='application/x-yaml')
    return exported
