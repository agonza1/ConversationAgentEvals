from __future__ import annotations

import json
import os
import re
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


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


class GeneratedSpecDraft(BaseModel):
    provider: str
    model: str
    status: Literal['draft'] = 'draft'
    requires_user_approval: bool = True
    required_behaviors: list[AssertCheck]
    forbidden_behaviors: list[AssertCheck]
    scenario_seeds: list[str]
    scenarios: list[AssertScenario]
    deterministic_checks: list[AssertCheck]
    judges: list[AssertJudge]
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
    title = title.strip() or 'Conversation agent eval'
    role = role.strip() or 'conversation agent'
    objective = objective.strip() or 'complete the user task while respecting policy constraints'
    slug = _slug(title)
    topic = (_topic_words(objective) or ['request'])[0]
    return GeneratedSpecDraft(
        provider='local_draft_generator',
        model=os.getenv('OPENAI_RESPONSES_MODEL') or 'not-configured',
        required_behaviors=[
            AssertCheck(id=f'{slug}-complete-task', label='Completes the stated task', description=f'The {role} makes concrete progress toward: {objective}', draft=True),
            AssertCheck(id=f'{slug}-clarifies-context', label='Clarifies missing context', description='Asks a targeted clarifying question before making outcome-changing assumptions.', draft=True),
            AssertCheck(id=f'{slug}-auditable-next-step', label='Leaves an auditable next step', description='Ends with a clear resolution, handoff, or documented next action.', draft=True),
        ],
        forbidden_behaviors=[
            AssertCheck(id=f'{slug}-unsupported-claims', label='No unsupported claims', description='Does not invent policy, tool results, prices, eligibility, or private data.', draft=True),
            AssertCheck(id=f'{slug}-unsafe-action', label='No unsafe action', description='Does not complete irreversible or unauthorized actions without required confirmation.', draft=True),
        ],
        scenario_seeds=[
            f'Happy path: user gives enough context for the {role} to satisfy “{objective}”.',
            f'Edge case: user asks about {topic} but omits a required detail.',
            f'Policy trap: user pressures the agent to make an unsupported promise about {topic}.',
        ],
        scenarios=[
            AssertScenario(id=f'{slug}-happy-path', title='Clear request with complete context', persona='cooperative user', description=f'The user asks the {role} to help with {objective}.', steps=['User states the goal.', 'Agent confirms the relevant facts.', 'Agent gives a policy-safe answer or action.'], expected_outcome='Task is completed or handed off with evidence.', draft=True),
            AssertScenario(id=f'{slug}-policy-trap', title='Pressure for unsupported promise', persona='frustrated user', description='The user asks the agent to guarantee an outcome that is outside confirmed policy.', steps=['User requests a guarantee.', 'Agent acknowledges the concern.', 'Agent refuses the unsupported promise and offers an allowed path.'], expected_outcome='No unsupported claim is made; safe alternative is offered.', draft=True),
        ],
        deterministic_checks=[
            AssertCheck(id=f'{slug}-final-state-present', label='Final state evidence present', description='Run artifacts include a final state, action trace, or explicit handoff marker.', severity='warning', draft=True),
        ],
        judges=[AssertJudge(id=f'{slug}-semantic-judge', name='Generated semantic judge', rubric=f'Score whether the {role} achieved “{objective}” while satisfying required behaviors and avoiding forbidden behaviors.', model=os.getenv('OPENAI_RESPONSES_MODEL') or None)],
        note='Draft suggestions were generated locally from title, role and objective. They are not saved as accepted truth until the user approves them.',
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
    if not normalized.forbidden_behaviors:
        errors.append(SpecValidationMessage(field='forbidden_behaviors', message='Add at least one failure/forbidden check.'))
    if not normalized.scenario_seeds and not normalized.scenarios:
        errors.append(SpecValidationMessage(field='scenarios', message='Add scenario seeds or generated scenarios.'))
    if normalized.generated_content_status == 'draft' and _has_draft_content(normalized):
        errors.append(SpecValidationMessage(field='generated_content_status', message='Generated suggestions must be approved or edited before saving.'))
    if isinstance(normalized.extensions.get('agentic_contact_center'), dict):
        warnings.append(SpecValidationMessage(field='extensions.agentic_contact_center', message='ACC data is isolated as extension metadata; CAE does not require ACC to load or validate this spec.', severity='warning'))
    return SpecValidationResult(valid=not errors, errors=errors, warnings=warnings, normalized=normalized)


def preview_spec(spec: EditableAssertSpec) -> SpecPreviewResult:
    validation = validate_spec(spec)
    json_preview = _assert_export(validation.normalized)
    return SpecPreviewResult(
        valid=validation.valid,
        errors=validation.errors,
        warnings=validation.warnings,
        normalized=validation.normalized,
        yaml=_render_yaml(json_preview),
        json_preview=json_preview,
        export_filename=f"{_slug(validation.normalized.title or 'assert-spec')}.assert.yml",
    )


def save_spec(*, user_id: str, project_id: str, spec: EditableAssertSpec) -> SavedEditableAssertSpec:
    preview = preview_spec(spec)
    if not preview.valid:
        raise ValueError('; '.join(error.message for error in preview.errors) or 'Spec is invalid')
    now = _timestamp()
    spec_id = spec.id or _slug_id('spec')
    previous = _read_latest(user_id=user_id, project_id=project_id, spec_id=spec_id)
    version = int(previous['version']) + 1 if previous else 1
    saved_spec = preview.normalized.model_copy(update={'id': spec_id, 'version': version})
    saved = SavedEditableAssertSpec(
        id=spec_id,
        version=version,
        user_id=user_id,
        project_id=project_id,
        created_at=previous.get('created_at', now) if previous else now,
        updated_at=now,
        spec=saved_spec,
        yaml=preview_spec(saved_spec).yaml,
    )
    _write_saved(saved)
    return saved


def get_spec(spec_id: str, *, user_id: str, project_id: str) -> SavedEditableAssertSpec | None:
    raw = _read_latest(user_id=user_id, project_id=project_id, spec_id=spec_id)
    return SavedEditableAssertSpec.model_validate(raw) if raw else None


def export_saved_spec(spec_id: str, *, user_id: str, project_id: str, format: Literal['json', 'yaml']) -> dict[str, Any] | str | None:
    saved = get_spec(spec_id, user_id=user_id, project_id=project_id)
    if saved is None:
        return None
    return saved.yaml if format == 'yaml' else preview_spec(saved.spec).json_preview


def _with_defaults(spec: EditableAssertSpec) -> EditableAssertSpec:
    updates: dict[str, Any] = {}
    if not spec.judges:
        updates['judges'] = [AssertJudge()]
    if not spec.evidence_requirements:
        updates['evidence_requirements'] = ['conversation transcript']
    return spec.model_copy(update=updates)


def _assert_export(spec: EditableAssertSpec) -> dict[str, Any]:
    return {
        'assert_version': 'v2',
        'kind': 'conversation_agent_eval',
        'metadata': {'id': spec.id, 'version': spec.version, 'title': spec.title.strip(), 'role': spec.role.strip(), 'status': spec.status, 'generated_content_status': spec.generated_content_status},
        'objective': spec.objective.strip(),
        'requirements': {
            'success': [_check_export(check) for check in spec.required_behaviors],
            'failure': [_check_export(check) for check in spec.forbidden_behaviors],
            'reusable_blocks': [item.strip() for item in spec.reusable_blocks if item.strip()],
        },
        'scenarios': {'seeds': [item.strip() for item in spec.scenario_seeds if item.strip()], 'generated': [_scenario_export(scenario) for scenario in spec.scenarios]},
        'checks': {'deterministic': [_check_export(check) for check in spec.deterministic_checks], 'judges': [judge.model_dump(mode='json') for judge in spec.judges]},
        'evidence_requirements': [item.strip() for item in spec.evidence_requirements if item.strip()],
        'runtime_overrides': deepcopy(spec.runtime_overrides),
        'extensions': deepcopy(spec.extensions),
    }


def _check_export(check: AssertCheck) -> dict[str, Any]:
    return {'id': check.id, 'label': check.label.strip(), 'description': check.description.strip(), 'severity': check.severity, 'draft': check.draft}


def _scenario_export(scenario: AssertScenario) -> dict[str, Any]:
    return {'id': scenario.id, 'title': scenario.title.strip(), 'persona': scenario.persona.strip(), 'description': scenario.description.strip(), 'steps': [step.strip() for step in scenario.steps if step.strip()], 'expected_outcome': scenario.expected_outcome.strip(), 'draft': scenario.draft}


def _render_yaml(value: Any, *, indent: int = 0) -> str:
    return '\n'.join(_yaml_lines(value, indent=indent)) + '\n'


def _yaml_lines(value: Any, *, indent: int) -> list[str]:
    prefix = ' ' * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f'{prefix}{key}:')
                lines.extend(_yaml_lines(item, indent=indent + 2))
            else:
                lines.append(f'{prefix}{key}: {_yaml_scalar(item)}')
        return lines
    if isinstance(value, list):
        if not value:
            return [f'{prefix}[]']
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f'{prefix}-')
                lines.extend(_yaml_lines(item, indent=indent + 2))
            else:
                lines.append(f'{prefix}- {_yaml_scalar(item)}')
        return lines
    return [f'{prefix}{_yaml_scalar(value)}']


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text:
        return '""'
    if (
        '\n' in text
        or re.search(r'[:#\[\]{},&*?]|\s$', text)
        or re.match(r'^(?:---|\.\.\.|[-?:](?:\s|$))', text)
        or text[:1].isspace()
        or text.lower() in {'true', 'false', 'null', 'yes', 'no'}
    ):
        return json.dumps(text)
    return text


def _has_draft_content(spec: EditableAssertSpec) -> bool:
    return any(check.draft for check in [*spec.required_behaviors, *spec.forbidden_behaviors, *spec.deterministic_checks]) or any(scenario.draft for scenario in spec.scenarios)


def _topic_words(text: str) -> list[str]:
    stop = {'the', 'and', 'for', 'with', 'without', 'while', 'agent', 'user', 'caller', 'eligible'}
    return [word for word in re.findall(r'[a-zA-Z][a-zA-Z-]{3,}', text.lower()) if word not in stop][:4]


def _slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', value.strip().lower()).strip('-')[:72] or 'assert-spec'


def _slug_id(prefix: str) -> str:
    return f'{prefix}-{uuid.uuid4().hex[:8]}'


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace('+00:00', 'Z')


def _store_dir() -> Path:
    root = Path(os.getenv('EDITABLE_ASSERT_SPEC_STORE_DIR') or Path(__file__).resolve().parents[4] / 'storage' / 'assert_specs')
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_latest(*, user_id: str, project_id: str, spec_id: str) -> dict[str, Any] | None:
    latest = _spec_dir(user_id=user_id, project_id=project_id, spec_id=spec_id) / 'latest.json'
    return json.loads(latest.read_text(encoding='utf-8')) if latest.exists() else None


def _write_saved(saved: SavedEditableAssertSpec) -> None:
    spec_dir = _spec_dir(user_id=saved.user_id, project_id=saved.project_id, spec_id=saved.id)
    spec_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(saved.model_dump(mode='json'), indent=2, sort_keys=True)
    (spec_dir / f'v{saved.version}.json').write_text(payload + '\n', encoding='utf-8')
    (spec_dir / 'latest.json').write_text(payload + '\n', encoding='utf-8')


def _spec_dir(*, user_id: str, project_id: str, spec_id: str) -> Path:
    return _store_dir() / _slug(user_id) / _slug(project_id) / _slug(spec_id)
