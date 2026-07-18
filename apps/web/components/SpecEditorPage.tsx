'use client';

import { useEffect, useMemo, useState } from 'react';

import { SiteNav } from '@/components/SiteNav';
import {
  generateEditableAssertDraft,
  listEditableAssertTemplates,
  previewEditableAssertSpec,
  saveEditableAssertSpec,
} from '@/lib/api';
import {
  AssertCheck,
  AssertJudge,
  AssertScenario,
  EditableAssertPreview,
  EditableAssertSpec,
  EditableAssertTemplate,
  SavedEditableAssertSpec,
} from '@/lib/types';
import { demoProjectId, demoUserId } from '@/lib/execution';

const defaultJudge: AssertJudge = {
  id: 'semantic-policy-judge',
  name: 'Semantic policy judge',
  kind: 'semantic',
  rubric: 'Score whether the agent achieved the objective while satisfying success checks and avoiding forbidden checks.',
  weight: 1,
  provider: 'configured-default',
};

const starterSpec: EditableAssertSpec = {
  title: 'Cancellation rescue agent',
  role: 'insurance retention voice agent',
  objective: 'Save eligible callers without making unauthorized billing promises.',
  status: 'draft',
  generated_content_status: 'none',
  required_behaviors: [],
  forbidden_behaviors: [],
  reusable_blocks: [],
  scenario_seeds: [],
  scenarios: [],
  deterministic_checks: [],
  evidence_requirements: ['conversation transcript', 'final state or action trace'],
  judges: [defaultJudge],
  runtime_overrides: {},
  extensions: {},
};

function lines(value: string) {
  return value.split('\n').map((line) => line.trim()).filter(Boolean);
}

function slug(prefix: string, label: string, index: number) {
  return `${prefix}-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 42) || index + 1}`;
}

function checksFromText(value: string, existing: AssertCheck[], prefix: string, draft: boolean): AssertCheck[] {
  return lines(value).map((label, index) => ({
    ...(existing[index] || {}),
    id: existing[index]?.id || slug(prefix, label, index),
    label,
    description: existing[index]?.description || label,
    severity: existing[index]?.severity || (prefix === 'failure' ? 'error' : 'warning'),
    draft: draft || Boolean(existing[index]?.draft),
  }));
}

function scenariosFromText(value: string, existing: AssertScenario[], draft: boolean): AssertScenario[] {
  return lines(value).map((line, index) => {
    const [title, ...rest] = line.split(':');
    const description = rest.join(':').trim() || line;
    return {
      ...(existing[index] || {}),
      id: existing[index]?.id || slug('scenario', title, index),
      title: title.trim(),
      persona: existing[index]?.persona || '',
      description,
      steps: existing[index]?.steps || [],
      expected_outcome: existing[index]?.expected_outcome || description,
      draft: draft || Boolean(existing[index]?.draft),
    };
  });
}

function textFromChecks(checks: AssertCheck[]) {
  return checks.map((check) => check.label).join('\n');
}

function textFromScenarios(scenarios: AssertScenario[]) {
  return scenarios.map((scenario) => `${scenario.title}: ${scenario.description || scenario.expected_outcome || ''}`).join('\n');
}

export function SpecEditorPage() {
  const identity = useMemo(() => ({ userId: demoUserId(), projectId: demoProjectId() }), []);
  const [spec, setSpec] = useState<EditableAssertSpec>(starterSpec);
  const [templates, setTemplates] = useState<EditableAssertTemplate[]>([]);
  const [successChecks, setSuccessChecks] = useState('');
  const [failureChecks, setFailureChecks] = useState('');
  const [scenarioSeeds, setScenarioSeeds] = useState('');
  const [scenarios, setScenarios] = useState('');
  const [deterministicChecks, setDeterministicChecks] = useState('');
  const [evidenceRequirements, setEvidenceRequirements] = useState(starterSpec.evidence_requirements?.join('\n') || '');
  const [judgeRubric, setJudgeRubric] = useState(defaultJudge.rubric);
  const [generatedApproved, setGeneratedApproved] = useState(false);
  const [preview, setPreview] = useState<EditableAssertPreview | null>(null);
  const [saved, setSaved] = useState<SavedEditableAssertSpec | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'templates' | 'generate' | 'preview' | 'save' | null>(null);

  const workingSpec = useMemo<EditableAssertSpec>(() => {
    const draft = !generatedApproved && spec.generated_content_status === 'draft';
    return {
      ...spec,
      generated_content_status: spec.generated_content_status === 'draft' && generatedApproved ? 'approved' : spec.generated_content_status,
      required_behaviors: checksFromText(successChecks, spec.required_behaviors || [], 'success', draft),
      forbidden_behaviors: checksFromText(failureChecks, spec.forbidden_behaviors || [], 'failure', draft),
      scenario_seeds: lines(scenarioSeeds),
      scenarios: scenariosFromText(scenarios, spec.scenarios || [], draft),
      deterministic_checks: checksFromText(deterministicChecks, spec.deterministic_checks || [], 'deterministic', draft),
      evidence_requirements: lines(evidenceRequirements),
      judges: [{ ...(spec.judges?.[0] || defaultJudge), rubric: judgeRubric.trim() || defaultJudge.rubric }],
    };
  }, [deterministicChecks, evidenceRequirements, failureChecks, generatedApproved, judgeRubric, scenarioSeeds, scenarios, spec, successChecks]);
  const needsApproval = workingSpec.generated_content_status === 'draft' && !generatedApproved;

  useEffect(() => {
    let active = true;
    setBusy('templates');
    listEditableAssertTemplates()
      .then((next) => {
        if (active) setTemplates(next);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : 'Could not load templates');
      })
      .finally(() => {
        if (active) setBusy(null);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      setBusy((current) => current || 'preview');
      previewEditableAssertSpec(workingSpec)
        .then((next) => {
          if (active) setPreview(next);
        })
        .catch((err) => {
          if (active) setError(err instanceof Error ? err.message : 'Could not preview spec');
        })
        .finally(() => {
          if (active) setBusy((current) => (current === 'preview' ? null : current));
        });
    }, 350);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [workingSpec]);

  function loadTemplate(templateId: string) {
    const template = templates.find((item) => item.id === templateId);
    if (!template) return;
    applySpec(template.spec);
  }

  function applySpec(nextSpec: EditableAssertSpec) {
    setSpec(nextSpec);
    setSuccessChecks(textFromChecks(nextSpec.required_behaviors || []));
    setFailureChecks(textFromChecks(nextSpec.forbidden_behaviors || []));
    setScenarioSeeds((nextSpec.scenario_seeds || []).join('\n'));
    setScenarios(textFromScenarios(nextSpec.scenarios || []));
    setDeterministicChecks(textFromChecks(nextSpec.deterministic_checks || []));
    setEvidenceRequirements((nextSpec.evidence_requirements || []).join('\n'));
    setJudgeRubric(nextSpec.judges?.[0]?.rubric || defaultJudge.rubric);
    setGeneratedApproved(nextSpec.generated_content_status !== 'draft');
    setSaved(null);
  }

  async function generateDraft() {
    setBusy('generate');
    setError(null);
    try {
      const draft = await generateEditableAssertDraft({ title: spec.title, role: spec.role, objective: spec.objective });
      applySpec({
        ...spec,
        generated_content_status: 'draft',
        required_behaviors: draft.required_behaviors,
        forbidden_behaviors: draft.forbidden_behaviors,
        scenario_seeds: draft.scenario_seeds,
        scenarios: draft.scenarios,
        deterministic_checks: draft.deterministic_checks,
        judges: draft.judges,
      });
      setGeneratedApproved(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not generate suggestions');
    } finally {
      setBusy(null);
    }
  }

  async function saveVersion() {
    setBusy('save');
    setError(null);
    try {
      const next = await saveEditableAssertSpec({
        user_id: identity.userId,
        project_id: identity.projectId,
        spec: workingSpec,
      });
      setSaved(next);
      setSpec(next.spec);
      setGeneratedApproved(next.spec.generated_content_status !== 'draft');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save spec');
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="page-shell spec-editor-shell">
      <SiteNav current="specs" />
      <section className="minimal-hero spec-hero" aria-labelledby="spec-title">
        <p className="eyebrow">ASSERT spec builder · CAE-owned</p>
        <h1 id="spec-title">Friendly editable ASSERT YAML</h1>
        <p>Generate draft success checks, failure checks and scenarios from plain language, edit them, approve them, then preview and save versioned ASSERT YAML.</p>
      </section>

      <section className="spec-editor-toolbar card" aria-label="Spec editor controls">
        <label>
          Template
          <select value="" onChange={(event) => loadTemplate(event.target.value)} disabled={busy === 'templates'}>
            <option value="">Choose a starter template…</option>
            {templates.map((template) => <option key={template.id} value={template.id}>{template.label}</option>)}
          </select>
        </label>
        <button className="secondary-link" type="button" onClick={generateDraft} disabled={busy === 'generate'}>{busy === 'generate' ? 'Generating…' : 'Generate draft checks/scenarios'}</button>
        <button className="primary-link" type="button" onClick={() => setGeneratedApproved(true)} disabled={!needsApproval}>Approve generated draft</button>
        <button className="primary-link" type="button" onClick={saveVersion} disabled={busy === 'save'}>{busy === 'save' ? 'Saving…' : 'Save version'}</button>
        <span className="spec-workspace-context">Workspace: {identity.projectId}</span>
      </section>

      {error ? <div className="scenarios-error" role="alert">{error}</div> : null}
      {saved ? <div className="spec-save-banner" role="status">Saved `{saved.id}` version {saved.version}. YAML is ready to export or hand to ASSERT.</div> : null}

      <div className="spec-editor-grid">
        <section className="card spec-form-card" aria-label="Editable ASSERT fields">
          <div className="spec-field-row">
            <label>Title<input value={spec.title} onChange={(event) => setSpec({ ...spec, title: event.target.value })} /></label>
            <label>Agent role<input value={spec.role} onChange={(event) => setSpec({ ...spec, role: event.target.value })} /></label>
          </div>
          <label>Objective<textarea rows={3} value={spec.objective} onChange={(event) => setSpec({ ...spec, objective: event.target.value })} /></label>
          <div className="spec-field-row">
            <label>Success checks<textarea rows={8} value={successChecks} onChange={(event) => setSuccessChecks(event.target.value)} /></label>
            <label>Failure / forbidden checks<textarea rows={8} value={failureChecks} onChange={(event) => setFailureChecks(event.target.value)} /></label>
          </div>
          <div className="spec-field-row">
            <label>Scenario seeds<textarea rows={6} value={scenarioSeeds} onChange={(event) => setScenarioSeeds(event.target.value)} /></label>
            <label>Generated scenarios<textarea rows={6} value={scenarios} onChange={(event) => setScenarios(event.target.value)} /></label>
          </div>
          <div className="spec-field-row">
            <label>Deterministic checks<textarea rows={5} value={deterministicChecks} onChange={(event) => setDeterministicChecks(event.target.value)} /></label>
            <label>Evidence requirements<textarea rows={5} value={evidenceRequirements} onChange={(event) => setEvidenceRequirements(event.target.value)} /></label>
          </div>
          <label>Judge rubric<textarea rows={4} value={judgeRubric} onChange={(event) => setJudgeRubric(event.target.value)} /></label>
        </section>

        <aside className="spec-preview-panel" aria-label="YAML preview and validation">
          <div className="spec-preview-status">
            <span data-valid={preview?.valid === true}>{preview?.valid ? 'Valid preview' : 'Needs edits'}</span>
            <span>{workingSpec.generated_content_status === 'draft' ? 'Generated draft' : 'User-approved'}</span>
          </div>
          {needsApproval ? <div className="spec-approval-note">Generated suggestions are draft content. Edit them and click “Approve generated draft” before saving.</div> : null}
          {preview?.errors.length ? (
            <div className="spec-validation-list" role="alert"><strong>Inline validation</strong><ul>{preview.errors.map((item) => <li key={`${item.field}-${item.message}`}>{item.field}: {item.message}</li>)}</ul></div>
          ) : null}
          {preview?.warnings.length ? (
            <div className="spec-warning-list"><strong>Warnings</strong><ul>{preview.warnings.map((item) => <li key={`${item.field}-${item.message}`}>{item.message}</li>)}</ul></div>
          ) : null}
          <pre className="spec-yaml-preview">{preview?.yaml || 'YAML preview will appear here.'}</pre>
        </aside>
      </div>
    </main>
  );
}
