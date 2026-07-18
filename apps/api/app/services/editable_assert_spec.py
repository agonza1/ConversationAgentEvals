from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import EditableAssertSpecVersion, ProductProject, ProductWorkspaceMember
from app.services.llm_providers import get_provider
from app.services.ssl_util import verified_ssl_context


class AssertCheck(BaseModel):
    id: str = Field(default_factory=lambda: _slug_id('check'))
    label: str
    description: str = ''
    severity: Literal['info', 'warning', 'error'] = 'error'
    draft: bool = False


class AssertScenario(BaseModel):
    id: str = Field(default_factory=lambda: _slug_id('scenario'))
    title: str
    persona: str = ''
    description: str = ''
    steps: list[str] = Field(default_factory=list)
    expected_outcome: str = ''
    draft: bool = False


class AssertJudge(BaseModel):
    id: str = Field(default_factory=lambda: _slug_id('judge'))
    name: str = 'Semantic policy judge'
    kind: Literal['semantic', 'deterministic'] = 'semantic'
    rubric: str = 'Evaluate whether the agent satisfied required behaviors and avoided forbidden behaviors.'
    weight: float = Field(default=1.0, ge=0)
    provider: str = 'configured-default'
    model: str | None = None


class EditableAssertSpec(BaseModel):
    id: str | None = None
    version: int | None = None
    title: str = ''
    role: str = ''
    objective: str = ''
    status: Literal['draft', 'published'] = 'draft'
    generated_content_status: Literal['none', 'draft', 'approved'] = 'none'
    required_behaviors: list[AssertCheck] = Field(default_factory=list)
    forbidden_behaviors: list[AssertCheck] = Field(default_factory=list)
    reusable_blocks: list[str] = Field(default_factory=list)
    scenario_seeds: list[str] = Field(default_factory=list)
    scenarios: list[AssertScenario] = Field(default_factory=list)
    deterministic_checks: list[AssertCheck] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    judges: list[AssertJudge] = Field(default_factory=list)
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class SpecValidationMessage(BaseModel):
    field: str
    message: str
    severity: Literal['error', 'warning'] = 'error'


class SpecValidationResult(BaseModel):
    valid: bool
    errors: list[SpecValidationMessage] = Field(default_factory=list)
    warnings: list[SpecValidationMessage] = Field(default_factory=list)
    normalized: EditableAssertSpec


class SpecPreviewResult(SpecValidationResult):
    yaml: str
    json_preview: dict[str, Any]
    export_filename: str
    assert_validator: str = 'assert-ai'
    assert_validated: bool


class GeneratedSpecContent(BaseModel):
    required_behaviors: list[AssertCheck]
    forbidden_behaviors: list[AssertCheck]
    scenario_seeds: list[str]
    scenarios: list[AssertScenario]
    deterministic_checks: list[AssertCheck]
    judges: list[AssertJudge] = Field(max_length=1)


class GeneratedSpecDraft(GeneratedSpecContent):
    provider: str
    model: str
    status: Literal['draft'] = 'draft'
    requires_user_approval: bool = True
    note: str


class SavedEditableAssertSpec(BaseModel):
    id: str
    version: int
    user_id: str
    project_id: str
    created_at: str
    updated_at: str
    spec: EditableAssertSpec
    yaml: str


class SpecGenerationUnavailable(RuntimeError):
    pass


class SpecGenerationFailed(RuntimeError):
    pass


_SPEC_LOCKS_GUARD = threading.Lock()
_SPEC_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def default_templates() -> list[dict[str, Any]]:
    generic = EditableAssertSpec(
        title='Conversation agent quality gate',
        role='customer support conversation agent',
        objective='Resolve the user request accurately while following policy constraints.',
        required_behaviors=[AssertCheck(id='answer-user-request', label='Answers the user request', description='Responds to the core request with a useful next step.')],
        forbidden_behaviors=[AssertCheck(id='invent-policy', label='Does not invent policy', description='Avoids unsupported claims, guarantees, or internal-only promises.')],
        scenario_seeds=['A user asks for a policy-sensitive account change.'],
        evidence_requirements=['conversation transcript', 'final state or tool trace when tools are used'],
    )
    acc = EditableAssertSpec(
        title='Cancellation rescue agent',
        role='insurance retention voice agent',
        objective='Save eligible callers without making unauthorized billing promises.',
        required_behaviors=[
            AssertCheck(id='diagnose-reason', label='Diagnoses cancellation reason', description='Asks why the caller wants to cancel before offering retention steps.'),
            AssertCheck(id='offer-eligible-save', label='Offers eligible save path', description='Suggests policy-safe retention options when the caller is eligible.'),
        ],
        forbidden_behaviors=[
            AssertCheck(id='unauthorized-billing-promise', label='No unauthorized billing promises', description='Does not promise discounts, refunds, or billing changes outside policy.'),
        ],
        scenario_seeds=['Caller wants to cancel after a price increase.', 'Caller is angry about a recent claim denial.'],
        evidence_requirements=['transcript', 'action trace', 'final state', 'vCon export when available'],
        extensions={
            'agentic_contact_center': {
                'template': 'cancellation_rescue',
                'source_route': 'http://127.0.0.1:18036/assert/spec',
                'artifact_pointer_fields': ['call_id', 'proof_bundle_uri', 'vcon_uri'],
            }
        },
    )
    return [
        {'id': 'generic-conversation-agent', 'label': 'Generic conversation agent', 'description': 'CAE-native template with no target-specific dependency.', 'spec': _with_defaults(generic).model_dump(mode='json')},
        {'id': 'agentic-contact-center-cancellation-rescue', 'label': 'ACC cancellation-rescue handoff', 'description': 'Optional ACC prefill represented as extension metadata, not a core CAE dependency.', 'spec': _with_defaults(acc).model_dump(mode='json')},
    ]


def generate_spec_draft(*, title: str, role: str, objective: str) -> GeneratedSpecDraft:
    title = title.strip()
    role = role.strip()
    objective = objective.strip()
    if len(title) < 3 or len(role) < 3 or len(objective) < 10:
        raise ValueError('Add a clear title, agent role, and one-sentence objective before generating a draft.')

    prompt = _generation_prompt(title=title, role=role, objective=objective)
    raw, provider_name, model_name = _complete_generation(prompt)
    try:
        content = GeneratedSpecContent.model_validate(_parse_json_object(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SpecGenerationFailed(f'Configured model returned an invalid editable ASSERT draft: {exc}') from exc

    return GeneratedSpecDraft(
        provider=provider_name,
        model=model_name,
        required_behaviors=[item.model_copy(update={'draft': True}) for item in content.required_behaviors],
        forbidden_behaviors=[item.model_copy(update={'draft': True}) for item in content.forbidden_behaviors],
        scenario_seeds=[item.strip() for item in content.scenario_seeds if item.strip()],
        scenarios=[item.model_copy(update={'draft': True}) for item in content.scenarios],
        deterministic_checks=[item.model_copy(update={'draft': True}) for item in content.deterministic_checks],
        judges=content.judges,
        note='Draft suggestions came from the configured CAE LLM provider. They remain proposed content until the user approves them.',
    )


def validate_spec(spec: EditableAssertSpec) -> SpecValidationResult:
    normalized = _with_defaults(spec)
    errors: list[SpecValidationMessage] = []
    warnings: list[SpecValidationMessage] = []
    if len(normalized.title.strip()) < 3:
        errors.append(SpecValidationMessage(field='title', message='Add a short title for this eval spec.'))
    if len(normalized.role.strip()) < 3:
        errors.append(SpecValidationMessage(field='role', message='Describe the agent role being evaluated.'))
    if len(normalized.objective.strip()) < 10:
        errors.append(SpecValidationMessage(field='objective', message='Describe the task outcome in at least one sentence.'))
    if not normalized.required_behaviors:
        errors.append(SpecValidationMessage(field='required_behaviors', message='Add at least one success check.'))
    for index, check in enumerate(normalized.required_behaviors):
        if not check.label.strip():
            errors.append(SpecValidationMessage(field=f'required_behaviors.{index}.label', message='Success check labels cannot be blank.'))
    if not normalized.forbidden_behaviors:
        errors.append(SpecValidationMessage(field='forbidden_behaviors', message='Add at least one failure/forbidden check.'))
    for index, check in enumerate(normalized.forbidden_behaviors):
        if not check.label.strip():
            errors.append(SpecValidationMessage(field=f'forbidden_behaviors.{index}.label', message='Forbidden check labels cannot be blank.'))
    if not normalized.scenario_seeds and not normalized.scenarios:
        errors.append(SpecValidationMessage(field='scenarios', message='Add scenario seeds or generated scenarios.'))
    for index, seed in enumerate(normalized.scenario_seeds):
        if not seed.strip():
            errors.append(SpecValidationMessage(field=f'scenario_seeds.{index}', message='Scenario seeds cannot be blank.'))
    for index, scenario in enumerate(normalized.scenarios):
        if not scenario.title.strip():
            errors.append(SpecValidationMessage(field=f'scenarios.{index}.title', message='Scenario titles cannot be blank.'))
    if normalized.generated_content_status == 'draft' and _has_draft_content(normalized):
        errors.append(SpecValidationMessage(field='generated_content_status', message='Generated suggestions must be approved or edited before saving.'))
    if len(normalized.judges) > 1:
        errors.append(SpecValidationMessage(field='judges', message='ASSERT export currently supports exactly one configured judge. Remove additional judges before saving.'))
    if 'max_turns' in normalized.runtime_overrides and _coerce_max_turns(normalized.runtime_overrides.get('max_turns')) is None:
        errors.append(SpecValidationMessage(field='runtime_overrides.max_turns', message='max_turns must be a whole number from 1 through 100.'))
    if isinstance(normalized.extensions.get('agentic_contact_center'), dict):
        warnings.append(SpecValidationMessage(field='extensions.agentic_contact_center', message='ACC data is preserved in the CAE editor context; CAE does not require ACC to compile or validate this ASSERT config.', severity='warning'))
    return SpecValidationResult(valid=not errors, errors=errors, warnings=warnings, normalized=normalized)


def preview_spec(spec: EditableAssertSpec) -> SpecPreviewResult:
    validation = validate_spec(spec)
    config = _compile_assert_config(validation.normalized)
    assert_errors = _assert_validation_errors(config)
    errors = [*validation.errors, *assert_errors]
    return SpecPreviewResult(
        valid=not errors,
        errors=errors,
        warnings=validation.warnings,
        normalized=validation.normalized,
        yaml=_render_yaml(config),
        json_preview=config,
        export_filename=f"{_slug(validation.normalized.title or 'assert-spec')}.eval_config.yaml",
        assert_validated=not assert_errors,
    )


def save_spec(*, db: Session, user_id: str, project_id: str, spec: EditableAssertSpec) -> SavedEditableAssertSpec:
    preview = preview_spec(spec)
    if not preview.valid:
        raise ValueError('; '.join(error.message for error in preview.errors) or 'Spec is invalid')
    spec_id = spec.id or _slug_id('spec')
    project = _resolve_project(db, user_id=user_id, project_id=project_id)

    with _spec_lock(project.id, spec_id):
        for attempt in range(3):
            version = int(
                db.query(func.max(EditableAssertSpecVersion.version))
                .filter(
                    EditableAssertSpecVersion.project_id == project.id,
                    EditableAssertSpecVersion.spec_key == spec_id,
                )
                .scalar()
                or 0
            ) + 1
            saved_spec = preview.normalized.model_copy(update={'id': spec_id, 'version': version})
            saved_preview = preview_spec(saved_spec)
            record = EditableAssertSpecVersion(
                project_id=project.id,
                spec_key=spec_id,
                version=version,
                spec_json=json.dumps(saved_spec.model_dump(mode='json'), sort_keys=True),
                yaml=saved_preview.yaml,
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                if attempt == 2:
                    raise RuntimeError('Could not allocate an atomic ASSERT spec version after concurrent saves.')
                project = _resolve_project(db, user_id=user_id, project_id=project_id)
                continue
            db.refresh(record)
            return _saved_response(record=record, project=project)
    raise RuntimeError('Could not save ASSERT spec version.')


def get_spec(db: Session, spec_id: str, *, user_id: str, project_id: str) -> SavedEditableAssertSpec | None:
    project = _select_visible_project(_visible_projects(db, user_id=user_id, project_id=project_id), project_id=project_id)
    if project is None:
        return None
    row = (
        db.query(EditableAssertSpecVersion, ProductProject)
        .join(ProductProject, ProductProject.id == EditableAssertSpecVersion.project_id)
        .filter(
            ProductProject.id == project.id,
            EditableAssertSpecVersion.spec_key == spec_id,
        )
        .order_by(EditableAssertSpecVersion.version.desc())
        .first()
    )
    return _saved_response(record=row[0], project=row[1]) if row else None


def export_saved_spec(db: Session, spec_id: str, *, user_id: str, project_id: str, format: Literal['json', 'yaml']) -> dict[str, Any] | str | None:
    saved = get_spec(db, spec_id, user_id=user_id, project_id=project_id)
    if saved is None:
        return None
    if format == 'yaml':
        return saved.yaml
    persisted = yaml.safe_load(saved.yaml)
    return persisted if isinstance(persisted, dict) else None


def _with_defaults(spec: EditableAssertSpec) -> EditableAssertSpec:
    updates: dict[str, Any] = {}
    if not spec.judges:
        updates['judges'] = [AssertJudge()]
    if not spec.evidence_requirements:
        updates['evidence_requirements'] = ['conversation transcript']
    return spec.model_copy(update=updates)


def _compile_assert_config(spec: EditableAssertSpec) -> dict[str, Any]:
    model_name = next((judge.model for judge in spec.judges if judge.model), None) or os.getenv('ASSERT_DEFAULT_MODEL') or os.getenv('OPENAI_RESPONSES_MODEL') or 'openai/gpt-4.1-mini'
    behavior_sections = [
        f'# {spec.title.strip()}',
        '',
        spec.objective.strip(),
        '',
        '## Required behaviors',
        *[f'- {item.label.strip()}: {item.description.strip()}'.rstrip(': ') for item in spec.required_behaviors],
        '',
        '## Forbidden behaviors',
        *[f'- {item.label.strip()}: {item.description.strip()}'.rstrip(': ') for item in spec.forbidden_behaviors],
    ]
    if spec.evidence_requirements:
        behavior_sections.extend(['', '## Required evidence', *[f'- {item.strip()}' for item in spec.evidence_requirements if item.strip()]])
    if spec.scenario_seeds:
        behavior_sections.extend(['', '## Scenario seeds', *[f'- {item.strip()}' for item in spec.scenario_seeds if item.strip()]])
    if spec.scenarios:
        behavior_sections.extend(['', '## Approved scenarios'])
        for scenario in spec.scenarios:
            behavior_sections.extend([
                f'### {scenario.title.strip()}',
                f'Persona: {scenario.persona.strip() or "unspecified"}',
                scenario.description.strip(),
                *[f'- {step.strip()}' for step in scenario.steps if step.strip()],
                f'Expected outcome: {scenario.expected_outcome.strip()}',
            ])
    if spec.deterministic_checks:
        behavior_sections.extend([
            '',
            '## Deterministic checks',
            *[
                f'- [{item.severity}] {item.label.strip()}: {item.description.strip()}'.rstrip(': ')
                for item in spec.deterministic_checks
            ],
        ])

    context_lines = [f'Target role: {spec.role.strip()}']
    if spec.runtime_overrides:
        context_lines.append(f'CAE runtime overrides: {json.dumps(spec.runtime_overrides, sort_keys=True)}')
    if spec.extensions:
        context_lines.append(f'CAE integration extensions: {json.dumps(spec.extensions, sort_keys=True)}')

    pipeline: dict[str, Any] = {
        'systematize': {},
        'test_set': {
            'prompt': {'sample_size': max(1, len(spec.scenario_seeds))},
            'scenario': {'sample_size': max(1, len(spec.scenarios) or len(spec.scenario_seeds))},
        },
    }
    target = spec.runtime_overrides.get('target')
    if isinstance(target, dict) and target:
        pipeline['inference'] = {
            'target': deepcopy(target),
            'tester': {'model': {'name': str(spec.runtime_overrides.get('tester_model') or model_name)}},
            'max_turns': _coerce_max_turns(spec.runtime_overrides.get('max_turns')) or 10,
        }
        judge = spec.judges[0]
        pipeline['judge'] = {
            'model': {'name': judge.model or model_name},
            'dimensions': {
                _slug(judge.id): {
                    'description': judge.name,
                    'rubric': judge.rubric,
                }
            },
        }

    return {
        'suite': _slug(spec.id or spec.title),
        'behavior': {
            'name': _slug(spec.title),
            'description': '\n'.join(behavior_sections).strip(),
        },
        'context': '\n'.join(context_lines),
        'default_model': {'name': model_name},
        'artifacts_root': 'artifacts/assert',
        'results_dir': 'results',
        'pipeline': pipeline,
    }


def _assert_validation_errors(config: dict[str, Any]) -> list[SpecValidationMessage]:
    try:
        from assert_ai.config import load_runtime_context

        stage_modules = {
            'systematize': SimpleNamespace(SCOPE='suite'),
            'test_set': SimpleNamespace(SCOPE='suite'),
            'inference': SimpleNamespace(SCOPE='run'),
            'judge': SimpleNamespace(SCOPE='run'),
        }
        load_runtime_context(deepcopy(config), Path('/tmp/cae-assert/eval_config.yaml'), stage_modules=stage_modules)
    except Exception as exc:
        return [SpecValidationMessage(field='assert_config', message=f'ASSERT rejected the compiled eval_config: {exc}')]
    return []


def _render_yaml(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _coerce_max_turns(value: Any) -> int | None:
    if value is None:
        return 10
    if isinstance(value, bool):
        return None
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    if isinstance(value, str) and str(coerced) != value.strip():
        return None
    return coerced if 1 <= coerced <= 100 else None


def _resolve_project(db: Session, *, user_id: str, project_id: str) -> ProductProject:
    visible = _visible_projects(db, user_id=user_id, project_id=project_id)
    project = _select_visible_project(visible, project_id=project_id)
    if project is not None and project.user_id != user_id and not _can_edit_shared_project(db, project=project, user_id=user_id):
        raise ValueError('Workspace editor access is required to save a shared ASSERT spec.')
    if project is None:
        project = ProductProject(user_id=user_id, project_key=project_id, name=project_id.replace('-', ' ').title() or 'Default Project')
        db.add(project)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            project = (
                db.query(ProductProject)
                .filter(ProductProject.user_id == user_id, ProductProject.project_key == project_id)
                .one()
            )
    return project


def _visible_projects(db: Session, *, user_id: str, project_id: str) -> list[ProductProject]:
    workspace_ids = [
        workspace_id
        for (workspace_id,) in db.query(ProductWorkspaceMember.workspace_id)
        .filter(ProductWorkspaceMember.user_id == user_id)
        .all()
    ]
    query = db.query(ProductProject).filter((ProductProject.id == project_id) | (ProductProject.project_key == project_id))
    if workspace_ids:
        query = query.filter((ProductProject.user_id == user_id) | ProductProject.workspace_id.in_(workspace_ids))
    else:
        query = query.filter(ProductProject.user_id == user_id)
    return query.order_by(ProductProject.created_at.asc()).all()


def _select_visible_project(projects: list[ProductProject], *, project_id: str) -> ProductProject | None:
    if not projects:
        return None
    exact_id = next((project for project in projects if project.id == project_id), None)
    if exact_id is not None:
        return exact_id
    shared = [project for project in projects if project.workspace_id]
    return shared[0] if shared else projects[0]


def _can_edit_shared_project(db: Session, *, project: ProductProject, user_id: str) -> bool:
    if not project.workspace_id:
        return False
    member = (
        db.query(ProductWorkspaceMember)
        .filter(
            ProductWorkspaceMember.workspace_id == project.workspace_id,
            ProductWorkspaceMember.user_id == user_id,
        )
        .first()
    )
    return member is not None and member.role in {'owner', 'admin', 'editor'}


def _spec_lock(project_id: str, spec_id: str) -> threading.Lock:
    key = (project_id, spec_id)
    with _SPEC_LOCKS_GUARD:
        return _SPEC_LOCKS.setdefault(key, threading.Lock())


def _saved_response(*, record: EditableAssertSpecVersion, project: ProductProject) -> SavedEditableAssertSpec:
    spec = EditableAssertSpec.model_validate(json.loads(record.spec_json))
    created = record.created_at.replace(tzinfo=UTC).isoformat().replace('+00:00', 'Z')
    return SavedEditableAssertSpec(
        id=record.spec_key,
        version=record.version,
        user_id=project.user_id,
        project_id=project.project_key,
        created_at=created,
        updated_at=created,
        spec=spec,
        yaml=record.yaml,
    )


def _generation_prompt(*, title: str, role: str, objective: str) -> str:
    return '\n'.join([
        'Create a proposed conversation-agent evaluation draft as JSON.',
        'Return JSON only, with keys required_behaviors, forbidden_behaviors, scenario_seeds, scenarios, deterministic_checks, and judges.',
        'Each behavior/check needs id, label, description, and severity. Each scenario needs id, title, persona, description, steps, and expected_outcome.',
        'Each judge needs id, name, kind="semantic", rubric, weight=1, provider="configured-default", and model=null.',
        'Produce concrete, auditable checks and 2-4 realistic scenarios. Do not claim any content is already approved.',
        f'Title: {title}',
        f'Agent role: {role}',
        f'Objective: {objective}',
    ])


def _complete_generation(prompt: str) -> tuple[str, str, str]:
    provider = get_provider('openai')
    status = provider.status()
    model_name = (os.getenv('OPENAI_RESPONSES_MODEL') or os.getenv('LLM_JUDGE_MODEL') or 'gpt-5.4').strip()
    if status.get('status') == 'connected':
        try:
            return provider.complete(prompt, model_name=model_name), str(status.get('provider') or 'openai_codex'), model_name
        except Exception as exc:
            raise SpecGenerationFailed(f'Configured CAE model could not generate a draft: {exc}') from exc
    api_key = (os.getenv('LLM_JUDGE_API_KEY') or os.getenv('OPENAI_API_KEY') or '').strip()
    if not api_key:
        raise SpecGenerationUnavailable('Connect OpenAI Codex OAuth or configure OPENAI_API_KEY/LLM_JUDGE_API_KEY before generating draft checks and scenarios.')
    try:
        return _complete_with_api_key(prompt, api_key=api_key, model_name=model_name), 'openai_api_key', model_name
    except Exception as exc:
        raise SpecGenerationFailed(f'OpenAI could not generate an editable ASSERT draft: {exc}') from exc


def _complete_with_api_key(prompt: str, *, api_key: str, model_name: str) -> str:
    body = {
        'model': model_name,
        'messages': [
            {'role': 'system', 'content': 'You design rigorous conversation-agent evaluations and return strict JSON only.'},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.2,
        'response_format': {'type': 'json_object'},
    }
    request = urllib.request.Request(
        'https://api.openai.com/v1/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=60, context=verified_ssl_context()) as response:  # noqa: S310
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'OpenAI draft generation failed ({exc.code}): {detail}') from exc
    choices = payload.get('choices') if isinstance(payload, dict) else None
    message = choices[0].get('message') if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    content = message.get('content') if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError('OpenAI draft generation returned no JSON content.')
    return content


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or '').strip()
    if text.startswith('```'):
        text = text.strip('`').strip()
        if text.lower().startswith('json'):
            text = text[4:].strip()
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('response did not contain a JSON object')
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError('response JSON must be an object')
    return parsed


def _has_draft_content(spec: EditableAssertSpec) -> bool:
    return any(check.draft for check in [*spec.required_behaviors, *spec.forbidden_behaviors, *spec.deterministic_checks]) or any(scenario.draft for scenario in spec.scenarios)


def _slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', value.strip().lower()).strip('-')[:72] or 'assert-spec'


def _slug_id(prefix: str) -> str:
    return f'{prefix}-{uuid.uuid4().hex[:8]}'
