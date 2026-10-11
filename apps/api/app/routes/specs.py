from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.integrations.assert_runtime import (
    AssertRuntimeUnavailable,
    EXPECTED_ASSERT_VERSION,
    behavior_preset,
    behavior_presets,
    judge_preset,
    judge_presets,
    scenario_presets,
)

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
from app.services.spec_scenario_authoring import generate_case_drafts, publish_scenarios
from app.services.spec_generation_settings import generation_settings, save_generation_settings


router = APIRouter(prefix='/api/specs', tags=['specs'])


class SpecDraftGenerateRequest(BaseModel):
    title: str = ''
    role: str = ''
    objective: str = ''
    requirements: str = Field(default='', max_length=30000)
    permissible_behavior: str = Field(default='', max_length=10000)
    behavior_preset: str | None = None
    scenario_preset: str | None = None


class SpecEnvelope(BaseModel):
    spec: EditableAssertSpec


class SpecGenerationSettingsRequest(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=160)


@router.get('/generation-settings')
def get_generation_settings():
    try:
        return generation_settings()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail='Could not read draft-generation settings.') from exc


@router.patch('/generation-settings')
def patch_generation_settings(payload: SpecGenerationSettingsRequest):
    try:
        return save_generation_settings(payload.model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail='Could not persist draft-generation settings.') from exc


class SpecSaveRequest(BaseModel):
    user_id: str = Field(min_length=1)
    project_id: str = Field(default='default', min_length=1)
    spec: EditableAssertSpec


class SpecDuplicateRequest(BaseModel):
    user_id: str = Field(min_length=1)
    project_id: str = Field(default='default', min_length=1)
    new_id: str = Field(min_length=1)


class SpecCaseGenerateRequest(BaseModel):
    spec: EditableAssertSpec
    behavior_ids: list[str] = Field(min_length=1, max_length=20)
    samples_per_behavior: int = Field(default=3, ge=1, le=5)
    engine: Literal['assert', 'cae_configured_llm'] = 'cae_configured_llm'


class SpecScenarioPublishRequest(BaseModel):
    user_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    confirm: bool = False


@router.get('/templates')
def list_spec_templates():
    return {'templates': default_templates()}


@router.get('/assert-library/behaviors')
def list_assert_behavior_presets():
    try:
        return {'assert_version': EXPECTED_ASSERT_VERSION, 'behaviors': behavior_presets()}
    except AssertRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get('/assert-library/behaviors/{name}')
def get_assert_behavior_preset(name: str):
    try:
        return {'assert_version': EXPECTED_ASSERT_VERSION, 'behavior': behavior_preset(name)}
    except AssertRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('/assert-library/judges')
def list_assert_judge_presets():
    try:
        return {'assert_version': EXPECTED_ASSERT_VERSION, 'judges': judge_presets()}
    except AssertRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get('/assert-library/judges/{name}')
def get_assert_judge_preset(name: str):
    try:
        return {'assert_version': EXPECTED_ASSERT_VERSION, 'judge': judge_preset(name)}
    except AssertRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get('/assert-library/scenarios')
def list_assert_scenario_contexts():
    try:
        return {'assert_version': EXPECTED_ASSERT_VERSION, 'scenarios': scenario_presets()}
    except AssertRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post('/generate')
def generate_editable_spec_draft(payload: SpecDraftGenerateRequest):
    try:
        return generate_spec_draft(**payload.model_dump())
    except SpecGenerationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AssertRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SpecGenerationFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/validate')
def validate_editable_spec(payload: SpecEnvelope):
    return validate_spec(payload.spec)


@router.post('/generate-cases')
def generate_reviewable_cases(payload: SpecCaseGenerateRequest):
    try:
        return generate_case_drafts(payload.spec, behavior_ids=payload.behavior_ids,
                                    samples_per_behavior=payload.samples_per_behavior, engine=payload.engine)
    except SpecGenerationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AssertRuntimeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SpecGenerationFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/{spec_id}/publish-scenarios')
def publish_reviewed_scenarios(spec_id: str, payload: SpecScenarioPublishRequest, db: Session = Depends(get_db)):
    try:
        return publish_scenarios(db, spec_id=spec_id, **payload.model_dump())
    except SpecProjectAmbiguous as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
def get_editable_spec(spec_id: str, user_id: str = Query(min_length=1), project_id: str = Query(default='default', min_length=1), version: int | None = Query(default=None, ge=1), db: Session = Depends(get_db)):
    try:
        saved = get_spec(db, spec_id, user_id=user_id, project_id=project_id, version=version)
    except SpecProjectAmbiguous as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if exported is None:
        raise HTTPException(status_code=404, detail='Spec not found')
    if format == 'yaml':
        return Response(content=str(exported), media_type='application/x-yaml')
    return exported
