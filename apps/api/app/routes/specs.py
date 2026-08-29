from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db

from app.services.editable_assert_spec import (
    EditableAssertSpec,
    SpecGenerationFailed,
    SpecGenerationUnavailable,
    SpecProjectAmbiguous,
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


class SpecDuplicateRequest(BaseModel):
    user_id: str = Field(min_length=1)
    project_id: str = Field(default='default', min_length=1)
    new_id: str = Field(min_length=1)


@router.get('/templates')
def list_spec_templates():
    return {'templates': default_templates()}


@router.post('/generate')
def generate_editable_spec_draft(payload: SpecDraftGenerateRequest):
    try:
        return generate_spec_draft(title=payload.title, role=payload.role, objective=payload.objective)
    except SpecGenerationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SpecGenerationFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/validate')
def validate_editable_spec(payload: SpecEnvelope):
    return validate_spec(payload.spec)


@router.post('/preview')
def preview_editable_spec(payload: SpecEnvelope):
    return preview_spec(payload.spec)


@router.post('')
def create_editable_spec(payload: SpecSaveRequest, db: Session = Depends(get_db)):
    try:
        return save_spec(db=db, user_id=payload.user_id, project_id=payload.project_id, spec=payload.spec)
    except SpecProjectAmbiguous as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/{spec_id}')
def get_editable_spec(spec_id: str, user_id: str = Query(min_length=1), project_id: str = Query(default='default', min_length=1), db: Session = Depends(get_db)):
    try:
        saved = get_spec(db, spec_id, user_id=user_id, project_id=project_id)
    except SpecProjectAmbiguous as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if saved is None:
        raise HTTPException(status_code=404, detail='Spec not found')
    return saved


@router.patch('/{spec_id}')
def update_editable_spec(spec_id: str, payload: SpecSaveRequest, db: Session = Depends(get_db)):
    try:
        return save_spec(db=db, user_id=payload.user_id, project_id=payload.project_id, spec=payload.spec.model_copy(update={'id': spec_id}))
    except SpecProjectAmbiguous as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/{spec_id}/versions')
def create_editable_spec_version(spec_id: str, payload: SpecSaveRequest, db: Session = Depends(get_db)):
    try:
        return save_spec(db=db, user_id=payload.user_id, project_id=payload.project_id, spec=payload.spec.model_copy(update={'id': spec_id}))
    except SpecProjectAmbiguous as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/{spec_id}/duplicate')
def duplicate_editable_spec(spec_id: str, payload: SpecDuplicateRequest, db: Session = Depends(get_db)):
    try:
        source = get_spec(db, spec_id, user_id=payload.user_id, project_id=payload.project_id)
        if source is None:
            raise HTTPException(status_code=404, detail='Spec not found')
        duplicate = source.spec.model_copy(update={'id': payload.new_id, 'version': None, 'status': 'draft'})
        return save_spec(db=db, user_id=payload.user_id, project_id=payload.project_id, spec=duplicate)
    except SpecProjectAmbiguous as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/{spec_id}/export')
def export_editable_spec(
    spec_id: str,
    user_id: str = Query(min_length=1),
    project_id: str = Query(default='default', min_length=1),
    format: Literal['json', 'yaml'] = Query(default='yaml'),
    db: Session = Depends(get_db),
):
    try:
        exported = export_saved_spec(db, spec_id, user_id=user_id, project_id=project_id, format=format)
    except SpecProjectAmbiguous as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if exported is None:
        raise HTTPException(status_code=404, detail='Spec not found')
    if format == 'yaml':
        return Response(content=str(exported), media_type='application/x-yaml')
    return exported
