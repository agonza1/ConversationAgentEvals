'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';

import { SiteNav } from '@/components/SiteNav';
import {
  generateEditableAssertDraft,
  generateEditableAssertCases,
  listAssertScenarioContexts,
  getEditableAssertSpec,
  publishEditableAssertScenarios,
  listAssertBehaviorPresets,
  listAssertJudgePresets,
  listEditableAssertTemplates,
  previewEditableAssertSpec,
  saveEditableAssertSpec,
} from '@/lib/api';
import {
  AssertCheck,
  AssertJudge,
  AssertLibraryPreset,
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
  title: 'Conversation agent quality gate',
  role: 'customer support conversation agent',
  objective: 'Resolve the user request accurately while following policy constraints.',
  status: 'draft',
  generated_content_status: 'none',
  required_behaviors: [],
  forbidden_behaviors: [],
  reusable_blocks: [],
  scenario_seeds: [],
  scenarios: [],
  deterministic_checks: [],
  evidence_requirements: ['conversation transcript', 'final state or tool trace when tools are used'],
  judges: [defaultJudge],
  runtime_overrides: {},
  extensions: {},
};

type BuiltinJudgeDimension = 'policy_violation' | 'overrefusal';

function lines(value: string) {
  return value.split('\n').map((line) => line.trim()).filter(Boolean);
}

function slug(prefix: string, label: string, index: number) {
  return `${prefix}-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 42) || index + 1}`;
}

function textMatches<T>(labels: string[], existing: T[], labelOf: (item: T) => string): Array<T | undefined> {
  const consumed = new Set<number>();
  const matches = labels.map((label) => {
    const index = existing.findIndex((item, candidate) => !consumed.has(candidate) && labelOf(item) === label);
    if (index >= 0) consumed.add(index);
    return index;
  });
  // Same-length edits rename the remaining rules/cases in order. Exact matches
  // reserve identities first, so reordering or deletion cannot steal metadata.
  if (labels.length === existing.length) {
    const remaining = existing.map((_, index) => index).filter((index) => !consumed.has(index));
    for (let index = 0; index < matches.length; index += 1) {
      if (matches[index] < 0) matches[index] = remaining.shift() ?? -1;
    }
  }
  return matches.map((index) => index >= 0 ? existing[index] : undefined);
}

function allocateTextId(baseId: string, matched: boolean, reservedIds: Set<string>, usedIds: Set<string>) {
  let id = baseId;
  let suffix = 2;
  while (usedIds.has(id) || (!matched && reservedIds.has(id))) id = `${baseId}-${suffix++}`;
  usedIds.add(id);
  return id;
}

function checksFromText(value: string, existing: AssertCheck[], prefix: string, draft: boolean): AssertCheck[] {
  const labels = lines(value);
  const matches = textMatches(labels, existing, (item) => item.label);
  const reservedIds = new Set(existing.map((item) => item.id));
  const usedIds = new Set<string>();
  return labels.map((label, index) => {
    const matched = matches[index];
    const baseId = matched?.id || slug(prefix, label, index);
    const id = allocateTextId(baseId, Boolean(matched), reservedIds, usedIds);
    return {
      ...(matched || {}),
      id,
      label,
      description: matched?.description || label,
      severity: matched?.severity || (prefix === 'failure' ? 'error' : 'warning'),
      draft: draft || Boolean(matched?.draft),
    };
  });
}

function scenariosFromText(value: string, existing: AssertScenario[], draft: boolean): AssertScenario[] {
  const entries = lines(value);
  const matches = textMatches(entries.map((line) => line.split(':')[0].trim()), existing, (item) => item.title);
  const reservedIds = new Set(existing.map((item) => item.id));
  const usedIds = new Set<string>();
  return entries.map((line, index) => {
    const [title, ...rest] = line.split(':');
    const description = rest.join(':').trim() || line;
    const matched = matches[index];
    return {
      ...(matched || {}),
      id: allocateTextId(matched?.id || slug('scenario', title, index), Boolean(matched), reservedIds, usedIds),
      title: title.trim(),
      persona: matched?.persona || '',
      description,
      steps: matched?.steps || [],
      expected_outcome: matched?.expected_outcome || description,
      draft: draft || Boolean(matched?.draft),
    };
  });
}

function textFromChecks(checks: AssertCheck[]) {
  return checks.map((check) => check.label).join('\n');
}

function textFromScenarios(scenarios: AssertScenario[]) {
  return scenarios.map((scenario) => `${scenario.title}: ${scenario.description || scenario.expected_outcome || ''}`).join('\n');
}

function scaleFromText(value: string): AssertJudge['scale'] {
  const entries = lines(value).flatMap((line) => {
    const separator = line.indexOf(':');
    if (separator < 1) return [];
    const grade = line.slice(0, separator).trim();
    const label = line.slice(separator + 1).trim();
    return grade && label ? [[grade, label] as const] : [];
  });
  return entries.length ? { type: 'ordinal', values: Object.fromEntries(entries) } : null;
}

function textFromScale(scale: AssertJudge['scale']) {
  if (!scale || scale.type !== 'ordinal') return '';
  return Object.entries(scale.values).map(([grade, label]) => `${grade}: ${label}`).join('\n');
}

// Compare the submitted editor snapshot, not server-added defaults/id/version.
function editableFingerprint(spec: EditableAssertSpec) {
  const { id, version, ...content } = spec;
  function ordered(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(ordered);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, ordered(item)]));
    return value;
  }
  return JSON.stringify(ordered(content));
}

export function SpecEditorPage() {
  const identity = useMemo(() => ({ userId: demoUserId(), projectId: (typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('project_id') : null) || demoProjectId() }), []);
  const [spec, setSpec] = useState<EditableAssertSpec>(starterSpec);
  const [templates, setTemplates] = useState<EditableAssertTemplate[]>([]);
  const [behaviorPresets, setBehaviorPresets] = useState<AssertLibraryPreset[]>([]);
  const [judgePresets, setJudgePresets] = useState<AssertLibraryPreset[]>([]);
  const [scenarioContexts, setScenarioContexts] = useState<AssertLibraryPreset[]>([]);
  const [successChecks, setSuccessChecks] = useState('');
  const [failureChecks, setFailureChecks] = useState('');
  const [scenarioSeeds, setScenarioSeeds] = useState('');
  const [scenarios, setScenarios] = useState('');
  const [deterministicChecks, setDeterministicChecks] = useState('');
  const [evidenceRequirements, setEvidenceRequirements] = useState(starterSpec.evidence_requirements?.join('\n') || '');
  const [judgeRubric, setJudgeRubric] = useState(defaultJudge.rubric);
  const [judgeAllowsNotApplicable, setJudgeAllowsNotApplicable] = useState(false);
  const [judgeOrdinalScale, setJudgeOrdinalScale] = useState('');
  const [disabledBuiltinDimensions, setDisabledBuiltinDimensions] = useState<BuiltinJudgeDimension[]>([]);
  const [selectedJudgePresets, setSelectedJudgePresets] = useState<string[]>([]);
  const [generatedApproved, setGeneratedApproved] = useState(false);
  const [preview, setPreview] = useState<EditableAssertPreview | null>(null);
  const [saved, setSaved] = useState<SavedEditableAssertSpec | null>(null);
  const [savedFingerprint, setSavedFingerprint] = useState<string | null>(null);
  const [selectedBehaviors, setSelectedBehaviors] = useState<string[]>([]);
  const [publishedSuite, setPublishedSuite] = useState<{ suite_id: string; scenario_id: string } | null>(null);
  const [publishConfirmed, setPublishConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'templates' | 'generate' | 'cases' | 'publish' | 'preview' | 'save' | null>(null);

  const workingSpec = useMemo<EditableAssertSpec>(() => {
    const draft = !generatedApproved && spec.generated_content_status === 'draft';
    const baseJudge = spec.judges?.[0] || defaultJudge;
    const nextJudge: AssertJudge = {
      ...baseJudge,
      rubric: judgeRubric.trim() || defaultJudge.rubric,
    };
    const scale = scaleFromText(judgeOrdinalScale);
    if (judgeAllowsNotApplicable || baseJudge.allow_not_applicable !== undefined) {
      nextJudge.allow_not_applicable = judgeAllowsNotApplicable;
    }
    if (scale || baseJudge.scale !== undefined) {
      nextJudge.scale = scale;
    }
    if (disabledBuiltinDimensions.length || baseJudge.disabled_builtin_dimensions !== undefined) {
      nextJudge.disabled_builtin_dimensions = disabledBuiltinDimensions;
    }
    if (selectedJudgePresets.length || baseJudge.presets !== undefined) {
      nextJudge.presets = selectedJudgePresets;
    }
    return {
      ...spec,
      generated_content_status: spec.generated_content_status === 'draft' && generatedApproved ? 'approved' : spec.generated_content_status,
      required_behaviors: checksFromText(successChecks, spec.required_behaviors || [], 'success', draft),
      forbidden_behaviors: checksFromText(failureChecks, spec.forbidden_behaviors || [], 'failure', draft),
      scenario_seeds: lines(scenarioSeeds),
      scenarios: scenariosFromText(scenarios, spec.scenarios || [], draft),
      deterministic_checks: checksFromText(deterministicChecks, spec.deterministic_checks || [], 'deterministic', draft),
      evidence_requirements: lines(evidenceRequirements),
      judges: [nextJudge],
    };
  }, [deterministicChecks, disabledBuiltinDimensions, evidenceRequirements, failureChecks, generatedApproved, judgeAllowsNotApplicable, judgeOrdinalScale, judgeRubric, scenarioSeeds, scenarios, selectedJudgePresets, spec, successChecks]);
  const latestWorkingSpec = useRef(workingSpec);
  useEffect(() => {
    latestWorkingSpec.current = workingSpec;
  }, [workingSpec]);
  const needsApproval = workingSpec.generated_content_status === 'draft' && !generatedApproved;
  const unsavedChanges = !saved || savedFingerprint !== editableFingerprint(workingSpec);
  const checks = [...workingSpec.required_behaviors, ...workingSpec.forbidden_behaviors];
  const mutationBusy = busy !== null && busy !== 'preview';

  useEffect(() => {
    let active = true;
    setBusy('templates');
    Promise.all([
      listEditableAssertTemplates(),
      listAssertBehaviorPresets(),
      listAssertJudgePresets(),
      listAssertScenarioContexts(),
    ])
      .then(async ([nextTemplates, nextBehaviors, nextJudges, nextContexts]) => {
        if (!active) return;
        setTemplates(nextTemplates);
        setBehaviorPresets(nextBehaviors);
        setJudgePresets(nextJudges);
        setScenarioContexts(nextContexts);
        const query = new URLSearchParams(window.location.search);
        const specId = query.get('spec_id');
        if (specId) {
          const next = await getEditableAssertSpec(specId, identity.userId, query.get('project_id') || identity.projectId, Number(query.get('version')) || undefined);
          if (active) { applySpec(next.spec); setSaved(next); setSavedFingerprint(editableFingerprint(next.spec)); }
        }
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
  }, [identity.projectId, identity.userId]);

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
    const nextJudge = nextSpec.judges?.[0] || defaultJudge;
    setJudgeRubric(nextJudge.rubric || defaultJudge.rubric);
    setJudgeAllowsNotApplicable(Boolean(nextJudge.allow_not_applicable));
    setJudgeOrdinalScale(textFromScale(nextJudge.scale));
    setDisabledBuiltinDimensions(nextJudge.disabled_builtin_dimensions || []);
    setSelectedJudgePresets(nextJudge.presets || []);
    setGeneratedApproved(nextSpec.generated_content_status !== 'draft');
    setSaved(null);
    setSavedFingerprint(null);
    setPublishConfirmed(false);
    setPublishedSuite(null);
  }

  async function generateDraft() {
    const submitted = workingSpec;
    setBusy('generate');
    setError(null);
    try {
      const draft = await generateEditableAssertDraft({ title: submitted.title, role: submitted.role, objective: submitted.objective,
        requirements: submitted.requirements, permissible_behavior: submitted.permissible_behavior,
        behavior_preset: submitted.behavior_preset, scenario_preset: submitted.scenario_preset });
      if (latestWorkingSpec.current !== submitted) throw new Error('Design changed during generation. Draft discarded; generate again with the current requirements.');
      applySpec({
        ...submitted,
        generation_provenance: { engine: 'cae_configured_llm', provider: draft.provider, model: draft.model },
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
    const submittedSpec = workingSpec;
    setBusy('save');
    setError(null);
    try {
      const next = await saveEditableAssertSpec({
        user_id: identity.userId,
        project_id: identity.projectId,
        spec: submittedSpec,
      });
      if (latestWorkingSpec.current === submittedSpec) {
        applySpec(next.spec);
        setSavedFingerprint(editableFingerprint(next.spec));
      } else {
        setSavedFingerprint(null);
      }
      setSaved(next);
      setPublishConfirmed(false);
      setPublishedSuite(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save spec');
    } finally {
      setBusy(null);
    }
  }

  async function generateCases() {
    const submitted = workingSpec;
    setBusy('cases'); setError(null);
    try {
      const result = await generateEditableAssertCases({ spec: submitted, behavior_ids: selectedBehaviors, samples_per_behavior: 3 });
      if (latestWorkingSpec.current !== submitted) throw new Error('Design changed during generation. Case drafts discarded; try again.');
      applySpec({ ...submitted, scenarios: result.scenarios, generated_content_status: 'draft',
        generation_provenance: { engine: result.engine, provider: result.provider, model: result.model } });
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not generate cases'); }
    finally { setBusy(null); }
  }

  function updateCase(caseId: string, update: Partial<AssertScenario>) {
    setSpec({ ...workingSpec, scenarios: workingSpec.scenarios.map((item) => item.id === caseId ? { ...item, ...update } : item) });
  }

  function updateBehavior(checkId: string, update: Partial<AssertCheck>) {
    setSpec({ ...workingSpec,
      required_behaviors: workingSpec.required_behaviors.map((item) => item.id === checkId ? { ...item, ...update } : item),
      forbidden_behaviors: workingSpec.forbidden_behaviors.map((item) => item.id === checkId ? { ...item, ...update } : item),
    });
  }

  async function publishCases() {
    if (!saved || unsavedChanges || !publishConfirmed) return;
    const submitted = workingSpec;
    setBusy('publish'); setError(null);
    try {
      const result = await publishEditableAssertScenarios(saved.id, {
        user_id: identity.userId, project_id: saved.project_id, version: saved.version, confirm: true,
      });
      const scenarioId = result.scenario_ids[0];
      if (!scenarioId) throw new Error('The published suite returned no runnable scenarios.');
      if (latestWorkingSpec.current === submitted) setPublishedSuite({ suite_id: result.suite_id, scenario_id: scenarioId });
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not publish cases'); }
    finally { setBusy(null); }
  }

  return (
    <main className="page-shell spec-editor-shell">
      <SiteNav current="specs" />
      <section className="minimal-hero spec-hero" aria-labelledby="spec-title">
        <p className="eyebrow">Evaluation design · Experimental</p>
        <h1 id="spec-title">Create an evaluation design</h1>
        <p>Requirements → reviewed behaviors → runnable cases. Save a version, then publish its cases into Scenarios without starting a run.</p>
      </section>

      <section className="spec-editor-toolbar card" aria-label="Spec editor controls">
        <label>
          Template
          <select value="" onChange={(event) => loadTemplate(event.target.value)} disabled={busy === 'templates'}>
            <option value="">Choose a starter template…</option>
            {templates.map((template) => <option key={template.id} value={template.id}>{template.label}</option>)}
          </select>
        </label>
        <button className="secondary-link" type="button" onClick={generateDraft} disabled={mutationBusy}>{busy === 'generate' ? 'Generating…' : 'Generate draft checks/scenarios'}</button>
        <button className="primary-link" type="button" onClick={() => setGeneratedApproved(true)} disabled={!needsApproval}>Approve generated draft</button>
        <button className="primary-link" type="button" onClick={saveVersion} disabled={mutationBusy || needsApproval}>{busy === 'save' ? 'Saving…' : 'Save version'}</button>
        <span className="spec-workspace-context">Workspace: {identity.projectId}</span>
      </section>

      {error ? <div className="scenarios-error" role="alert">{error}</div> : null}
      {saved ? <div className="spec-save-banner" role="status">Saved `{saved.id}` version {saved.version}. YAML is ready to export or hand to ASSERT.</div> : null}

      <div className="spec-editor-grid">
        <section className="card spec-form-card" aria-label="Editable evaluation design fields">
          <div className="spec-field-row">
            <label>Title<input value={spec.title} onChange={(event) => setSpec({ ...spec, title: event.target.value })} /></label>
            <label>Agent role<input value={spec.role} onChange={(event) => setSpec({ ...spec, role: event.target.value })} /></label>
          </div>
          <label>Objective<textarea rows={3} value={spec.objective} onChange={(event) => setSpec({ ...spec, objective: event.target.value })} /></label>
          <label>Product requirements / policy<textarea rows={6} value={spec.requirements || ''} onChange={(event) => setSpec({ ...spec, requirements: event.target.value })} placeholder="The agent offers housing options. It must never sell a house or handle payments." /></label>
          <label>Permissible behavior boundary<textarea rows={3} value={spec.permissible_behavior || ''} onChange={(event) => setSpec({ ...spec, permissible_behavior: event.target.value })} placeholder="May explain options and costs, but cannot execute a sale or collect payment." /></label>
          <div className="spec-field-row">
            <label>Success checks<textarea rows={8} value={successChecks} onChange={(event) => setSuccessChecks(event.target.value)} /></label>
            <label>Failure / forbidden checks<textarea rows={8} value={failureChecks} onChange={(event) => setFailureChecks(event.target.value)} /></label>
          </div>
          {checks.map((check, index) => <div key={check.id}>
            <label>Behavior definition {index + 1}: {check.label}<textarea rows={2} value={check.description} onChange={(event) => updateBehavior(check.id, { description: event.target.value })} /><small>ID: {check.id}</small></label>
            <label>Source quotation for behavior {index + 1} (optional)<input value={check.source_quote || ''} onChange={(event) => updateBehavior(check.id, { source_quote: event.target.value })} /></label>
          </div>)}
          <div className="spec-field-row">
            <label>Scenario guidance<textarea rows={6} value={scenarioSeeds} onChange={(event) => setScenarioSeeds(event.target.value)} /></label>
            <label>Scenario examples<textarea rows={6} value={scenarios} onChange={(event) => setScenarios(event.target.value)} /></label>
          </div>
          <section aria-label="Runnable case authoring">
            <h2>Reviewed runnable cases</h2>
            <p>Select behaviors to generate three drafts each: normal, boundary, and adversarial. Generation replaces the current cases. Edit and approve before saving.</p>
            <ul>{checks.map((check) => <li key={check.id}>{check.label}: {workingSpec.scenarios.filter((item) => item.behavior_id === check.id).length} cases · {Array.from(new Set(workingSpec.scenarios.filter((item) => item.behavior_id === check.id).map((item) => item.variant || 'normal'))).join(', ') || 'not covered'}</li>)}</ul>
            <label>Behaviors to cover<select multiple size={Math.min(6, Math.max(2, checks.length))} value={selectedBehaviors} onChange={(event) => setSelectedBehaviors(Array.from(event.currentTarget.selectedOptions, (option) => option.value))}>
              {checks.map((check) => <option key={check.id} value={check.id}>{check.label}</option>)}
            </select></label>
            <button type="button" className="secondary-link" onClick={generateCases} disabled={mutationBusy || needsApproval || !selectedBehaviors.length || !spec.permissible_behavior?.trim()}>{busy === 'cases' ? 'Generating cases…' : 'Generate runnable case drafts'}</button>
            {workingSpec.scenarios.map((item, index) => <fieldset key={item.id}>
              <legend>{item.title || `Case ${index + 1}`}</legend>
              <label>Target behavior for case {index + 1}<select value={item.behavior_id || ''} onChange={(event) => updateCase(item.id, { behavior_id: event.target.value || null })}>
                <option value="">Choose one behavior…</option>{checks.map((check) => <option key={check.id} value={check.id}>{check.label}</option>)}
              </select></label>
              <label>Variant for case {index + 1}<select value={item.variant || 'normal'} onChange={(event) => updateCase(item.id, { variant: event.target.value as AssertScenario['variant'] })}>
                <option value="normal">Normal</option><option value="boundary">Boundary</option><option value="adversarial">Adversarial</option>
              </select></label>
              <label>Caller instructions for case {index + 1}<textarea rows={3} value={(item.steps || []).join('\n')} onChange={(event) => updateCase(item.id, { steps: event.target.value.split('\n') })} /></label>
              <small>First line is the opening caller utterance. Remaining lines guide the adaptive tester, not the target agent.</small>
              <label>Expected outcome for case {index + 1}<textarea rows={2} value={item.expected_outcome || ''} onChange={(event) => updateCase(item.id, { expected_outcome: event.target.value })} /></label>
            </fieldset>)}
            <p>Generation uses CAE’s configured LLM; it does not run ASSERT’s inference pipeline. Rule-only evaluation stays Needs review for authored policies; optional ASSERT semantic judgment is separate.</p>
            <label className="spec-check-option"><input type="checkbox" checked={publishConfirmed} onChange={(event) => setPublishConfirmed(event.target.checked)} />I reviewed the saved rules and cases; publish this version to the shared local catalog.</label>
            <button className="primary-link" type="button" onClick={publishCases} disabled={mutationBusy || needsApproval || unsavedChanges || !publishConfirmed}>{busy === 'publish' ? 'Publishing…' : 'Publish saved cases to Scenarios'}</button>
            {unsavedChanges ? <p>Save the current design before publishing.</p> : null}
            {publishedSuite ? <p role="status">Published version {saved?.version}. <Link href={{ pathname: '/scenarios', query: publishedSuite }}>View runnable scenarios</Link> · <Link href={{ pathname: '/runs', query: publishedSuite }}>Choose a target and run</Link></p> : null}
          </section>
          <div className="spec-field-row">
            <label>Programmatic check guidance (not yet enforced)<textarea rows={5} value={deterministicChecks} onChange={(event) => setDeterministicChecks(event.target.value)} /></label>
            <label>Evidence guidance (not yet enforced)<textarea rows={5} value={evidenceRequirements} onChange={(event) => setEvidenceRequirements(event.target.value)} /></label>
          </div>
          <label>Judge rubric<textarea rows={4} value={judgeRubric} onChange={(event) => setJudgeRubric(event.target.value)} /></label>
          <section className="spec-assert-options" aria-labelledby="assert-options-title">
            <div>
              <p className="eyebrow">ASSERT 0.3 settings</p>
              <h2 id="assert-options-title">Preset and scoring controls</h2>
            </div>
            <div className="spec-field-row">
              <label>
                Behavior preset
                <select
                  value={spec.behavior_preset || ''}
                  onChange={(event) => setSpec({ ...spec, behavior_preset: event.target.value || null })}
                >
                  <option value="">Custom CAE behavior only</option>
                  {behaviorPresets.map((preset) => <option key={preset.name} value={preset.name}>{preset.name.replaceAll('_', ' ')}</option>)}
                </select>
                {spec.behavior_preset ? <small>{behaviorPresets.find((preset) => preset.name === spec.behavior_preset)?.summary || 'Combined with this design’s custom behavior contract.'}</small> : null}
              </label>
              <label>Application context preset<select value={spec.scenario_preset || ''} onChange={(event) => setSpec({ ...spec, scenario_preset: event.target.value || null })}>
                <option value="">Custom application context</option>{scenarioContexts.map((item) => <option key={item.name} value={item.name}>{item.name.replaceAll('_', ' ')}</option>)}
              </select><small>ASSERT context library; not an executable scenario. {scenarioContexts.find((item) => item.name === spec.scenario_preset)?.summary}</small></label>
              <label>
                Judge presets
                <select
                  multiple
                  size={Math.min(5, Math.max(2, judgePresets.length))}
                  value={selectedJudgePresets}
                  onChange={(event) => setSelectedJudgePresets(Array.from(event.currentTarget.selectedOptions, (option) => option.value))}
                >
                  {judgePresets.map((preset) => <option key={preset.name} value={preset.name}>{preset.name.replaceAll('-', ' ')}</option>)}
                </select>
                <small>Select one or more ASSERT dimensions; Ctrl/Cmd-click changes a multi-selection.</small>
              </label>
            </div>
            <div className="spec-field-row">
              <fieldset className="spec-judge-flags">
                <legend>Judge behavior</legend>
                <label className="spec-check-option">
                  <input type="checkbox" checked={judgeAllowsNotApplicable} onChange={(event) => setJudgeAllowsNotApplicable(event.target.checked)} />
                  Allow not applicable
                </label>
                {(['policy_violation', 'overrefusal'] as BuiltinJudgeDimension[]).map((dimension) => (
                  <label className="spec-check-option" key={dimension}>
                    <input
                      type="checkbox"
                      checked={disabledBuiltinDimensions.includes(dimension)}
                      onChange={(event) => setDisabledBuiltinDimensions((current) => (
                        event.target.checked
                          ? [...new Set([...current, dimension])]
                          : current.filter((item) => item !== dimension)
                      ))}
                    />
                    Disable {dimension.replaceAll('_', ' ')}
                  </label>
                ))}
              </fieldset>
              <label>
                Ordinal scale
                <textarea
                  rows={5}
                  placeholder={'unresolved: Unresolved\npartial: Partially resolved\nresolved: Resolved'}
                  value={judgeOrdinalScale}
                  onChange={(event) => setJudgeOrdinalScale(event.target.value)}
                />
                <small>Optional. Use one string grade and label per line: grade: label.</small>
              </label>
            </div>
          </section>
        </section>

        <aside className="spec-preview-panel" aria-label="Advanced ASSERT preview and validation">
          <p className="eyebrow">Advanced ASSERT preview</p>
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
