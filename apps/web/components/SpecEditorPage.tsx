'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import styles from './SpecEditorPage.module.css';

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
  evidence_requirements: ['transcript'],
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
      generation_provenance: matched?.generation_provenance || { engine: 'manual' },
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
  const router = useRouter();
  const identity = useMemo(() => ({ userId: demoUserId(), projectId: (typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('project_id') : null) || demoProjectId() }), []);
  const [spec, setSpec] = useState<EditableAssertSpec>(starterSpec);
  const [caseGenerator, setCaseGenerator] = useState<'assert' | 'cae_configured_llm'>('assert');
  const [templates, setTemplates] = useState<EditableAssertTemplate[]>([]);
  const [behaviorPresets, setBehaviorPresets] = useState<AssertLibraryPreset[]>([]);
  const [judgePresets, setJudgePresets] = useState<AssertLibraryPreset[]>([]);
  const [scenarioContexts, setScenarioContexts] = useState<AssertLibraryPreset[]>([]);
  const [successChecks, setSuccessChecks] = useState('');
  const [failureChecks, setFailureChecks] = useState('');
  const [scenarioSeeds, setScenarioSeeds] = useState('');
  const [scenarios, setScenarios] = useState('');
  const [scenarioExamplesEdited, setScenarioExamplesEdited] = useState(false);
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
  const continueInFlight = useRef(false);
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
      scenarios: scenarioExamplesEdited && !spec.scenarios.some((item) => item.generation_provenance?.engine === 'assert')
        ? scenariosFromText(scenarios, spec.scenarios || [], draft)
        : (spec.scenarios || []).map((caseDraft) => ({ ...caseDraft, draft: draft || Boolean(caseDraft.draft) })),
      deterministic_checks: checksFromText(deterministicChecks, spec.deterministic_checks || [], 'deterministic', draft),
      evidence_requirements: lines(evidenceRequirements),
      judges: [nextJudge],
    };
  }, [deterministicChecks, disabledBuiltinDimensions, evidenceRequirements, failureChecks, generatedApproved, judgeAllowsNotApplicable, judgeOrdinalScale, judgeRubric, scenarioSeeds, scenarios, scenarioExamplesEdited, selectedJudgePresets, spec, successChecks]);
  const latestWorkingSpec = useRef(workingSpec);
  useEffect(() => {
    latestWorkingSpec.current = workingSpec;
  }, [workingSpec]);
  const needsApproval = workingSpec.generated_content_status === 'draft' && !generatedApproved;
  const unsavedChanges = !saved || savedFingerprint !== editableFingerprint(workingSpec);
  const checks = [...workingSpec.required_behaviors, ...workingSpec.forbidden_behaviors];
  const validSelectedBehaviors = selectedBehaviors.filter((id) => checks.some((check) => check.id === id));
  const coveredRules = checks.filter((check) => workingSpec.scenarios.some((item) => item.behavior_id === check.id));
  const hasAssertCases = workingSpec.scenarios.some((item) => item.generation_provenance?.engine === 'assert');
  const runnableCases = workingSpec.scenarios.length > 0 && workingSpec.scenarios.every((item) =>
    item.steps?.[0]?.trim() && item.expected_outcome?.trim() && checks.some((check) => check.id === item.behavior_id));
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
    setScenarioExamplesEdited(false);
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
    if (!validSelectedBehaviors.length || validSelectedBehaviors.length > 6) return;
    setBusy('cases'); setError(null);
    try {
      const result = await generateEditableAssertCases({ spec: approvedSnapshot(submitted), behavior_ids: validSelectedBehaviors, samples_per_behavior: 3, engine: caseGenerator });
      if (latestWorkingSpec.current !== submitted) throw new Error('Design changed during generation. Case drafts discarded; try again.');
      applySpec({ ...submitted, scenarios: result.scenarios, generated_content_status: 'draft',
        generation_provenance: { ...result.provenance, engine: result.engine, provider: result.provider, model: result.model } });
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not generate cases'); }
    finally { setBusy(null); }
  }

  function approvedSnapshot(snapshot: EditableAssertSpec): EditableAssertSpec {
    return { ...snapshot, generated_content_status: 'approved',
      required_behaviors: snapshot.required_behaviors.map((item) => ({ ...item, draft: false })),
      forbidden_behaviors: snapshot.forbidden_behaviors.map((item) => ({ ...item, draft: false })),
      deterministic_checks: (snapshot.deterministic_checks || []).map((item) => ({ ...item, draft: false })),
      scenarios: snapshot.scenarios.map((item) => ({ ...item, draft: false })),
    };
  }

  async function approveAndContinue() {
    if (continueInFlight.current || mutationBusy || !runnableCases) return;
    continueInFlight.current = true;
    const submitted = workingSpec;
    const submittedFingerprint = editableFingerprint(submitted);
    setError(null); setBusy('save');
    try {
      let version = saved && !unsavedChanges && !needsApproval ? saved : null;
      if (!version) {
        version = await saveEditableAssertSpec({ user_id: identity.userId, project_id: identity.projectId,
          spec: approvedSnapshot(submitted) });
        setSaved(version);
        if (editableFingerprint(latestWorkingSpec.current) !== submittedFingerprint) {
          setSavedFingerprint(null);
          throw new Error('The submitted test set was saved, but you made newer edits. Review those edits before continuing; nothing was published.');
        }
        applySpec(version.spec);
        latestWorkingSpec.current = version.spec;
        setSaved(version); setSavedFingerprint(editableFingerprint(version.spec));
      }
      const versionFingerprint = editableFingerprint(version.spec);
      setBusy('publish');
      const result = await publishEditableAssertScenarios(version.id, {
        user_id: identity.userId, project_id: version.project_id, version: version.version, confirm: true,
      });
      const scenarioId = result.scenario_ids[0];
      if (!scenarioId) throw new Error('The published suite returned no runnable scenarios.');
      setPublishedSuite({ suite_id: result.suite_id, scenario_id: scenarioId });
      if (editableFingerprint(latestWorkingSpec.current) !== versionFingerprint) {
        throw new Error('The saved version was published, but newer edits were not included. Review the current test set before continuing.');
      }
      const query = new URLSearchParams({ suite_id: result.suite_id, scenario_id: scenarioId, run_scope: 'suite' });
      router.push(`/runs?${query}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not prepare test set. Retry to publish the saved version.');
    } finally { continueInFlight.current = false; setBusy(null); }
  }

  function updateCase(caseId: string, update: Partial<AssertScenario>) {
    const next = { ...workingSpec, scenarios: workingSpec.scenarios.map((item) => item.id === caseId ? { ...item, ...update } : item) };
    setSpec(next);
    if (!scenarioExamplesEdited) setScenarios(textFromScenarios(next.scenarios));
  }

  function addManualCase() {
    const next = { ...workingSpec, scenarios: [...workingSpec.scenarios, {
      id: `manual-${crypto.randomUUID()}`, title: `Manual case ${workingSpec.scenarios.length + 1}`,
      description: '', steps: [''], expected_outcome: '', behavior_id: null,
      variant: 'normal' as const, generation_provenance: { engine: 'manual' },
    }] };
    setSpec(next); setScenarios(textFromScenarios(next.scenarios)); setScenarioExamplesEdited(false);
  }

  function removeCase(caseId: string) {
    const next = { ...workingSpec, scenarios: workingSpec.scenarios.filter((item) => item.id !== caseId) };
    setSpec(next); setScenarios(textFromScenarios(next.scenarios)); setScenarioExamplesEdited(false);
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
    <main className={`page-shell spec-editor-shell ${styles.shell}`}>
      <SiteNav current="specs" />
      <section className="minimal-hero spec-hero" aria-labelledby="spec-title">
        <p className="eyebrow">Evaluation design · Experimental</p>
        <h1 id="spec-title">Create an evaluation design</h1>
        <p>Define the rules, review caller cases, then choose a voice agent and run the test set.</p>
      </section>
      <ol className={styles.steps} aria-label="Evaluation workflow">
        <li aria-current={workingSpec.scenarios.length ? undefined : 'step'}>1 · Define test</li>
        <li aria-current={workingSpec.scenarios.length ? 'step' : undefined}>2 · Review cases</li>
        <li>3 · Run voice tests</li><li>4 · Inspect results</li>
      </ol>
      {error ? <div className="scenarios-error" role="alert">{error}</div> : null}
      {saved ? <div className="spec-save-banner" role="status">Saved `{saved.id}` version {saved.version}.{unsavedChanges ? ' Current edits are not saved.' : ' Ready to publish; retrying will reuse this version.'}</div> : null}

      <section className="card spec-form-card" aria-labelledby="define-test-title">
        <h2 id="define-test-title">1 · Define test</h2>
        <div className="spec-field-row">
          <label>Title<input value={spec.title} onChange={(event) => setSpec({ ...spec, title: event.target.value })} /></label>
          <label>Agent role<input value={spec.role} onChange={(event) => setSpec({ ...spec, role: event.target.value })} /></label>
        </div>
        <label>Objective<textarea rows={2} value={spec.objective} onChange={(event) => setSpec({ ...spec, objective: event.target.value })} /></label>
        <label>Product requirements / policy<textarea rows={3} value={spec.requirements || ''} onChange={(event) => setSpec({ ...spec, requirements: event.target.value })} placeholder="The rules the agent must follow." /></label>
        <label>Permissible behavior boundary<textarea rows={2} value={spec.permissible_behavior || ''} onChange={(event) => setSpec({ ...spec, permissible_behavior: event.target.value })} placeholder="What may the agent do, and where must it stop?" /></label>
        <div className="spec-field-row">
          <label>Success checks<textarea rows={3} value={successChecks} onChange={(event) => setSuccessChecks(event.target.value)} placeholder="What must happen? One rule per line." /></label>
          <label>Failure / forbidden checks<textarea rows={3} value={failureChecks} onChange={(event) => setFailureChecks(event.target.value)} placeholder="What must never happen? One rule per line." /></label>
        </div>
        <fieldset className={styles.coverage}>
          <legend>Behaviors to cover</legend>
          <p>Choose up to 6 reviewed rules. Each gets a normal, boundary, and adversarial caller case.</p>
          {checks.length ? checks.map((check) => <label key={check.id} className="spec-check-option">
            <input type="checkbox" checked={validSelectedBehaviors.includes(check.id)}
              disabled={mutationBusy || (!validSelectedBehaviors.includes(check.id) && validSelectedBehaviors.length >= 6)}
              onChange={(event) => setSelectedBehaviors((current) => event.target.checked ? [...new Set([...current, check.id])] : current.filter((id) => id !== check.id))} />
            {check.label}
          </label>) : <p>Add a success or forbidden check above to generate targeted cases.</p>}
          <strong role="status">{validSelectedBehaviors.length} rules selected · {validSelectedBehaviors.length * 3} cases · maximum 20 per request</strong>
        </fieldset>
        {needsApproval ? <p className="spec-approval-note">Generated suggestions are draft content. Review the rules; generating cases approves the submitted rules, and the new cases remain drafts.</p> : null}
        <div className={styles.actions}>
          <button type="button" className="primary-link" onClick={generateCases} disabled={mutationBusy || !validSelectedBehaviors.length || validSelectedBehaviors.length > 6 || !spec.permissible_behavior?.trim()}>
            {busy === 'cases' ? 'Generating cases…' : workingSpec.scenarios.length ? needsApproval ? 'Approve rules and regenerate test cases' : 'Regenerate test cases' : needsApproval ? 'Approve rules and generate test cases' : 'Generate test cases'}
          </button>
          <button type="button" className="secondary-link" onClick={addManualCase} disabled={mutationBusy}>Add manual case</button>
        </div>
        {workingSpec.scenarios.length ? <small>Regeneration replaces {workingSpec.scenarios.length} {workingSpec.scenarios.length === 1 ? 'case' : 'cases'}, including your edits, with {validSelectedBehaviors.length * 3} new cases. Save a version first if you want to keep them.</small> : null}
        <small>{caseGenerator === 'assert' ? 'ASSERT generates caller prompts using the model in Console Settings. Expected outcomes come from your rules; agent responses are not prewritten.' : 'CAE custom generation uses its prompt template and the model in Console Settings.'}</small>
      </section>

      <section className="card spec-form-card" aria-labelledby="review-cases-title">
        <h2 id="review-cases-title">2 · Review cases <span className={styles.count}>{workingSpec.scenarios.length}</span></h2>
        <p>Check each caller opening and expected outcome. Targeted case coverage: {coveredRules.length} of {checks.length} rules.</p>
        {checks.length ? <ul className={styles.coverageList}>{checks.map((check) => {
          const focused = workingSpec.scenarios.filter((item) => item.behavior_id === check.id);
          return <li key={check.id}><span>{check.label}</span><strong data-covered={focused.length > 0}>{focused.length ? `${focused.length} focused cases · ${Array.from(new Set(focused.map((item) => item.variant || 'normal'))).join(', ')}` : 'No targeted cases'}</strong></li>;
        })}</ul> : null}
        <small>Every case retains the full policy. A rule with no targeted cases has no dedicated coverage yet.</small>
        {workingSpec.scenarios.length ? <div className={styles.cases}>{workingSpec.scenarios.map((item, index) => <fieldset key={item.id} className={styles.caseCard}>
          <legend>{index + 1} · {item.title || `Case ${index + 1}`} <span className={styles.variant}>{item.variant || 'normal'}</span></legend>
          {item.generation_provenance?.engine === 'assert' ? <small>ASSERT {item.generation_provenance.assert_version} · caller prompt</small> : null}
          {item.generation_provenance?.engine === 'assert' ? <label>Opening caller message for case {index + 1}<textarea rows={3} value={item.steps?.[0] || ''} onChange={(event) => updateCase(item.id, { steps: [event.target.value, ...(item.steps || []).slice(1)] })} /></label>
            : <label>Caller instructions for case {index + 1}<textarea rows={3} value={(item.steps || []).join('\n')} onChange={(event) => updateCase(item.id, { steps: event.target.value.split('\n') })} /><small>First line is the opening; remaining lines guide the adaptive tester.</small></label>}
          <label>Expected outcome for case {index + 1}<textarea rows={2} value={item.expected_outcome || ''} onChange={(event) => updateCase(item.id, { expected_outcome: event.target.value })} /></label>
          <details className={styles.details}>
            <summary>Case {index + 1} details</summary>
            <div className={styles.detailBody}>
              <label>Case title {index + 1}<input value={item.title} onChange={(event) => updateCase(item.id, { title: event.target.value })} /></label>
              <label>Target behavior for case {index + 1}<select value={item.behavior_id || ''} onChange={(event) => updateCase(item.id, { behavior_id: event.target.value || null })}>
                <option value="">Choose one behavior…</option>{checks.map((check) => <option key={check.id} value={check.id}>{check.label}</option>)}
              </select></label>
              <label>Variant for case {index + 1}<select value={item.variant || 'normal'} onChange={(event) => updateCase(item.id, { variant: event.target.value as AssertScenario['variant'] })}>
                <option value="normal">Normal</option><option value="boundary">Boundary</option><option value="adversarial">Adversarial</option>
              </select></label>
              {item.generation_provenance?.engine === 'assert' ? <label>Follow-up caller instructions for case {index + 1}<textarea rows={2} value={(item.steps || []).slice(1).join('\n')} onChange={(event) => updateCase(item.id, { steps: [item.steps?.[0] || '', ...lines(event.target.value)] })} /><small>The whole opening is one utterance. Follow-up instructions guide compatible adaptive testers.</small></label> : null}
              <button type="button" className="secondary-link" onClick={() => removeCase(item.id)} disabled={mutationBusy} aria-label={`Remove case ${index + 1}`}>Remove case</button>
            </div>
          </details>
        </fieldset>)}</div> : <p>Generate cases or add one manually to start reviewing.</p>}
        <div className={styles.continueBar}>
          <p>Continuing confirms you reviewed the rules and cases. CAE saves an immutable version and publishes that version, then opens target selection. No voice call starts yet.</p>
          <button className="primary-link" type="button" onClick={approveAndContinue} disabled={mutationBusy || !runnableCases}>
            {busy === 'save' ? 'Saving approved test set…' : busy === 'publish' ? 'Publishing test set…' : 'Approve test set and continue'}
          </button>
          {!runnableCases && workingSpec.scenarios.length ? <small>Each case needs a caller opening, an expected outcome, and one target behavior.</small> : null}
        </div>
        {publishedSuite ? <p role="status">Published version {saved?.version}. <Link href={{ pathname: '/scenarios', query: publishedSuite }}>View runnable scenarios</Link> · <Link href={{ pathname: '/runs', query: { ...publishedSuite, run_scope: 'suite' } }}>Choose a target and run</Link></p> : null}
      </section>

      <details className={`card ${styles.advanced}`}>
        <summary>Advanced · templates, custom generation, scoring and YAML</summary>
        <div className={`spec-form-card ${styles.detailBody}`}>
          <div className={styles.actions}>
            <label>Template<select value="" onChange={(event) => loadTemplate(event.target.value)} disabled={mutationBusy}>
              <option value="">Choose a starter template…</option>{templates.map((template) => <option key={template.id} value={template.id}>{template.label}</option>)}
            </select></label>
            <button className="secondary-link" type="button" onClick={generateDraft} disabled={mutationBusy}>{busy === 'generate' ? 'Generating…' : 'Generate draft checks/scenarios'}</button>
            <button className="secondary-link" type="button" onClick={() => setGeneratedApproved(true)} disabled={!needsApproval || mutationBusy}>Approve generated draft</button>
            <button className="secondary-link" type="button" onClick={saveVersion} disabled={mutationBusy || needsApproval}>{busy === 'save' ? 'Saving…' : 'Save version'}</button>
          </div>
          <small>Loading a template or generating draft checks/scenarios replaces the current rules and cases. Save a version first to keep your reviewed work.</small>
          <span className="spec-workspace-context">Workspace: {identity.projectId}</span>
          <label>Case generator<select value={caseGenerator} onChange={(event) => setCaseGenerator(event.target.value as typeof caseGenerator)} disabled={mutationBusy}>
            <option value="assert">ASSERT test generation</option><option value="cae_configured_llm">CAE custom generation</option>
          </select></label>
          {checks.map((check, index) => <div key={check.id} className={styles.detailBody}>
            <label>Behavior definition {index + 1}: {check.label}<textarea rows={2} value={check.description} onChange={(event) => updateBehavior(check.id, { description: event.target.value })} /><small>ID: {check.id}</small></label>
            <label>Source quotation for behavior {index + 1} (optional)<input value={check.source_quote || ''} onChange={(event) => updateBehavior(check.id, { source_quote: event.target.value })} /></label>
          </div>)}
          <label>Scenario guidance<textarea rows={3} value={scenarioSeeds} onChange={(event) => setScenarioSeeds(event.target.value)} /></label>
          {!hasAssertCases ? <label>Scenario examples<textarea rows={3} value={scenarios} onChange={(event) => { setScenarios(event.target.value); setScenarioExamplesEdited(true); }} /></label> : null}
          <div className="spec-field-row">
            <label>Programmatic checks<textarea rows={5} value={deterministicChecks} onChange={(event) => setDeterministicChecks(event.target.value)} /></label>
            <label>Required evidence<textarea rows={5} value={evidenceRequirements} onChange={(event) => setEvidenceRequirements(event.target.value)} /></label>
          </div>
          <p>One expression per line: transcript_present, action_trace_present, final_state_present, final_state_complete, tool_succeeded:exact_tool_name, or transcript_contains:literal text. These inspect recorded evidence, not semantic meaning. Required evidence: transcript, action_trace, final_state, vcon, or final_state_or_action_trace. Unsupported entries and missing evidence block verification.</p>
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
          <details className={styles.details}>
            <summary>Saved-version publication controls</summary>
            <div className={styles.detailBody}>
              <label className="spec-check-option"><input type="checkbox" checked={publishConfirmed} onChange={(event) => setPublishConfirmed(event.target.checked)} />I reviewed the saved rules and cases; publish this version to the shared local catalog.</label>
              <button className="secondary-link" type="button" onClick={publishCases} disabled={mutationBusy || needsApproval || unsavedChanges || !publishConfirmed}>{busy === 'publish' ? 'Publishing…' : 'Publish saved cases to Scenarios'}</button>
              {unsavedChanges ? <p>Save the current design before publishing.</p> : null}
            </div>
          </details>
          <aside className="spec-preview-panel" aria-label="Advanced ASSERT preview and validation">
            <p className="eyebrow">Advanced ASSERT preview</p>
            <div className="spec-preview-status"><span data-valid={preview?.valid === true}>{preview?.valid ? 'Valid preview' : 'Needs edits'}</span><span>{workingSpec.generated_content_status === 'draft' ? 'Generated draft' : 'User-approved'}</span></div>
            {preview?.errors.length ? <div className="spec-validation-list" role="alert"><strong>Inline validation</strong><ul>{preview.errors.map((item) => <li key={`${item.field}-${item.message}`}>{item.field}: {item.message}</li>)}</ul></div> : null}
            {preview?.warnings.length ? <div className="spec-warning-list"><strong>Warnings</strong><ul>{preview.warnings.map((item) => <li key={`${item.field}-${item.message}`}>{item.field}: {item.message}</li>)}</ul></div> : null}
            <pre className="spec-yaml-preview">{preview?.yaml || 'YAML preview will appear here.'}</pre>
          </aside>
        </div>
      </details>
    </main>
  );
}
