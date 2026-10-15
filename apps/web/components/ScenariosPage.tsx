'use client';

import Link from 'next/link';
import { ApiAwareLink } from '@/components/ApiAwareLink';
import { AssistedTestBuilder } from '@/components/AssistedTestBuilder';
import { quickCaseFromDraft, type AuthoringContext } from '@/lib/assistedAuthoring';
import type { EditableAssertSpec } from '@/lib/types';
import { getApiBase } from '@/lib/execution';
import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

type JsonRecord = Record<string, unknown>;

interface ScenarioRecord {
  id: string;
  suite_id: string;
  title: string;
  description: string;
  user_persona: string;
  user_goal: string;
  simulated_user_prompt: string;
  spoken_opener: string;
  expected_output: string;
  constraints: string[];
  required_actions: string[];
  forbidden_actions: string[];
  expected_final_state: string;
  source: string;
  evaluation_spec_ref: string;
  caller_steps: string[];
  target_behavior_id: string;
  policy_context: string;
  evidence_requirements: string[];
  requirements: string;
  permissible_behavior: string;
}

interface ScenarioSuite {
  id: string;
  title: string;
  description: string;
  scenarios: ScenarioRecord[];
}


async function handleJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    let message = text || `Request failed with ${response.status}`;
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (typeof parsed.detail === 'string') message = parsed.detail;
    } catch {
      // Keep the plain-text response.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

function stringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).map((item) => item.trim()).filter(Boolean);
  if (typeof value === 'string') return value.split(/\n|;/).map((item) => item.trim()).filter(Boolean);
  return [];
}

function displayValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return '';
  return JSON.stringify(value, null, 2);
}

function normalizeScenario(value: unknown, suiteId: string): ScenarioRecord {
  const record = asRecord(value);
  return {
    id: String(record.id ?? record.scenario_id ?? ''),
    suite_id: String(record.suite_id ?? suiteId),
    title: String(record.title ?? record.name ?? 'Untitled scenario'),
    description: String(record.description ?? ''),
    user_persona: String(record.user_persona ?? record.persona ?? ''),
    user_goal: String(record.user_goal ?? record.goal ?? ''),
    simulated_user_prompt: String(record.simulated_user_prompt ?? record.prompt ?? record.persona ?? ''),
    spoken_opener: String(record.spoken_opener ?? ''),
    expected_output: displayValue(record.expected_output ?? record.expected_final_state),
    constraints: stringList(record.constraints),
    required_actions: stringList(record.required_actions),
    forbidden_actions: stringList(record.forbidden_actions),
    expected_final_state: displayValue(record.expected_final_state ?? record.expected_output),
    source: String(record.source ?? ''),
    evaluation_spec_ref: displayValue(record.evaluation_spec_ref),
    caller_steps: stringList(record.caller_steps),
    target_behavior_id: String(record.target_behavior_id ?? ''),
    policy_context: Array.isArray(record.behaviors) ? record.behaviors.map((value) => {
      const rule = asRecord(value);
      return `${rule.kind}: ${rule.label} [${rule.id}]\n${rule.description || ''}`;
    }).join('\n\n') : '',
    evidence_requirements: stringList(record.evidence_requirements),
    requirements: String(record.requirements ?? ''),
    permissible_behavior: String(record.permissible_behavior ?? ''),
  };
}

function normalizeSuites(value: unknown): ScenarioSuite[] {
  const record = asRecord(value);
  const rawSuites = Array.isArray(value) ? value : Array.isArray(record.suites) ? record.suites : [];
  return rawSuites.map((item) => {
    const suite = asRecord(item);
    const id = String(suite.id ?? suite.suite_id ?? '');
    const rawScenarios = [
      ...(Array.isArray(suite.scenarios) ? suite.scenarios : []),
      ...(Array.isArray(suite.optional_scenarios) ? suite.optional_scenarios : []),
    ];
    return {
      id,
      title: String(suite.title ?? suite.name ?? 'Untitled suite'),
      description: String(suite.description ?? ''),
      scenarios: rawScenarios.map((scenario) => normalizeScenario(scenario, id)),
    };
  });
}

async function listScenarioSuites(signal?: AbortSignal): Promise<ScenarioSuite[]> {
  return normalizeSuites(await handleJson<unknown>(
    await fetch(`${getApiBase()}/api/benchmarks/suites`, { cache: 'no-store', signal }),
  ));
}

async function createScenario(payload: {
  title?: string;
  simulated_user_prompt: string;
  spoken_opener: string;
  expected_output: string;
  description: string;
  required_actions: string[];
  forbidden_actions: string[];
  evidence_requirements?: string[];
  requirements?: string;
  permissible_behavior?: string;
  caller_steps?: string[];
}): Promise<ScenarioRecord> {
  const value = await handleJson<unknown>(
    await fetch(`${getApiBase()}/api/scenarios`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
  return normalizeScenario(value, 'user-scenarios');
}

async function deleteScenario(scenarioId: string): Promise<void> {
  await handleJsonOrEmpty(
    await fetch(`${getApiBase()}/api/scenarios/${encodeURIComponent(scenarioId)}`, {
      method: 'DELETE',
    }),
  );
}

async function handleJsonOrEmpty(response: Response): Promise<void> {
  if (response.ok) return;
  await handleJson<unknown>(response);
}

async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Fall through to the legacy clipboard path.
    }
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    if (!document.execCommand('copy')) throw new Error('Clipboard copy failed.');
  } finally {
    textarea.remove();
  }
}

function scenarioKey(suiteId: string, scenarioId: string) {
  return `${suiteId}:${scenarioId}`;
}

function scenarioHref(path: '/runs' | '/eval', scenario: ScenarioRecord) {
  const query: Record<string, string> = { suite_id: scenario.suite_id, scenario_id: scenario.id };
  if (path === '/eval') query.sample = '1';
  if (typeof window !== 'undefined') {
    const apiBase = new URLSearchParams(window.location.search).get('api_base');
    if (apiBase) query.api_base = apiBase;
  }
  return { pathname: path, query };
}

function FieldCard({ title, value, onCopy }: { title: string; value: string; onCopy: (label: string, text: string) => void }) {
  return (
    <article className="scenario-field-card">
      <div className="scenario-field-card-header">
        <h3>{title}</h3>
        <button type="button" className="scenario-copy-button" aria-label={`Copy ${title}`} onClick={() => onCopy(title, value)}>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="5" y="5" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <rect x="3" y="3" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
          </svg>
        </button>
      </div>
      <p>{value || 'None declared.'}</p>
    </article>
  );
}

function listText(items: string[]) {
  return items.length ? items.map((item) => `• ${item}`).join('\n') : '';
}

export function ScenariosPage() {
  const [suites, setSuites] = useState<ScenarioSuite[]>([]);
  const [selectedKey, setSelectedKey] = useState('');
  const [mode, setMode] = useState<'create' | 'view'>('view');
  const [manualCreate, setManualCreate] = useState(false);
  const [authoringSession, setAuthoringSession] = useState(0);
  const [variation, setVariation] = useState<AuthoringContext | null>(null);
  const [manualContext, setManualContext] = useState<(Pick<ReturnType<typeof quickCaseFromDraft>, 'evidence_requirements' | 'requirements' | 'permissible_behavior'> & { caller_steps?: string[] }) | null>(null);
  const [title, setTitle] = useState('');
  const [simulatedUserPrompt, setSimulatedUserPrompt] = useState('');
  const [spokenOpener, setSpokenOpener] = useState('');
  const [expectedOutput, setExpectedOutput] = useState('');
  const [description, setDescription] = useState('');
  const [requiredActions, setRequiredActions] = useState('');
  const [forbiddenActions, setForbiddenActions] = useState('');
  const [mirrorPrompt, setMirrorPrompt] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const loadRequestRef = useRef(0);
  const preferredSelectionRef = useRef('');
  const detailRef = useRef<HTMLElement | null>(null);

  const selected = useMemo(() => {
    for (const suite of suites) {
      const scenario = suite.scenarios.find((item) => scenarioKey(suite.id, item.id) === selectedKey);
      if (scenario) return { suite, scenario };
    }
    return null;
  }, [selectedKey, suites]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('create') === '1') {
      setMode('create');
    }
  }, []);

  useEffect(() => {
    const requestId = loadRequestRef.current + 1;
    loadRequestRef.current = requestId;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 12000);

    async function load() {
      setIsLoading(true);
      setError(null);
      try {
        const next = await listScenarioSuites(controller.signal);
        if (loadRequestRef.current !== requestId) return;
        setSuites(next);
        const params = new URLSearchParams(window.location.search);
        const requestedSuite = next.find((suite) => suite.id === params.get('suite_id'));
        const querySelection = params.get('suite_id') && (params.get('scenario_id') || requestedSuite?.scenarios[0]?.id)
          ? scenarioKey(String(params.get('suite_id')), String(params.get('scenario_id') || requestedSuite?.scenarios[0]?.id))
          : '';
        const wanted = preferredSelectionRef.current || querySelection;
        preferredSelectionRef.current = '';
        const available = new Set(next.flatMap((suite) => suite.scenarios.map((scenario) => scenarioKey(suite.id, scenario.id))));
        const first = next.flatMap((suite) => suite.scenarios.map((scenario) => scenarioKey(suite.id, scenario.id)))[0] ?? '';
        setSelectedKey(available.has(wanted) ? wanted : first);
      } catch (err) {
        if (loadRequestRef.current !== requestId) return;
        setSuites([]);
        setError(err instanceof Error && err.name === 'AbortError'
          ? 'Timed out loading scenarios. Check that the API is running and reachable.'
          : err instanceof Error ? err.message : 'Could not load scenarios');
      } finally {
        window.clearTimeout(timeoutId);
        if (loadRequestRef.current === requestId) setIsLoading(false);
      }
    }

    void load();
    return () => {
      controller.abort();
      window.clearTimeout(timeoutId);
    };
  }, [reloadKey]);

  useEffect(() => {
    if (mirrorPrompt && mode === 'create') setDescription(simulatedUserPrompt);
  }, [mirrorPrompt, mode, simulatedUserPrompt]);

  async function onCopy(label: string, text: string) {
    try {
      await copyText(text);
      setCopyMessage(`Copied ${label}.`);
    } catch (err) {
      setCopyMessage(err instanceof Error ? err.message : 'Copy failed.');
    }
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    setSaveMessage(null);
    try {
      const created = await createScenario({
        title: title.trim() || undefined,
        simulated_user_prompt: manualContext?.caller_steps
          ? [simulatedUserPrompt.trim(), ...manualContext.caller_steps].filter(Boolean).join('\n')
          : simulatedUserPrompt.trim(),
        spoken_opener: manualContext?.caller_steps ? manualContext.caller_steps[0].trim() : spokenOpener.trim(),
        expected_output: expectedOutput.trim(),
        description: description.trim(),
        required_actions: stringList(requiredActions),
        forbidden_actions: stringList(forbiddenActions),
        ...(manualContext || {}),
      });
      preferredSelectionRef.current = scenarioKey('user-scenarios', created.id);
      setMode('view');
      setSaveMessage('Scenario created in the User Scenarios evaluation suite.');
      setTitle('');
      setSimulatedUserPrompt('');
      setSpokenOpener('');
      setExpectedOutput('');
      setDescription('');
      setRequiredActions('');
      setForbiddenActions('');
      setMirrorPrompt(true);
      setManualContext(null);
      setReloadKey((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create scenario');
    } finally {
      setIsSaving(false);
    }
  }

  async function createAssistedCase(spec: EditableAssertSpec) {
    setIsSaving(true); setError(null); setSaveMessage(null);
    try {
      const created = await createScenario(quickCaseFromDraft(spec));
      preferredSelectionRef.current = scenarioKey('user-scenarios', created.id);
      setMode('view'); setVariation(null);
      setSaveMessage('Reviewed case created. Choose Test an agent when you are ready—no call has started.');
      setReloadKey((value) => value + 1);
    } finally { setIsSaving(false); }
  }

  function editAssistedCase(spec?: EditableAssertSpec) {
    if (spec) {
      const next = quickCaseFromDraft(spec);
      setTitle(next.title); setSimulatedUserPrompt(spec.scenarios[0].persona || '');
      setSpokenOpener(next.spoken_opener);
      setExpectedOutput(next.expected_output); setDescription(next.description);
      setRequiredActions(next.required_actions.join('\n')); setForbiddenActions(next.forbidden_actions.join('\n'));
      setMirrorPrompt(false);
      setManualContext({ evidence_requirements: next.evidence_requirements, requirements: next.requirements, permissible_behavior: next.permissible_behavior, caller_steps: next.caller_steps });
    }
    setManualCreate(true);
  }

  function beginQuickCase() {
    setTitle(''); setSimulatedUserPrompt(''); setSpokenOpener(''); setExpectedOutput(''); setDescription('');
    setRequiredActions(''); setForbiddenActions(''); setMirrorPrompt(true); setManualContext(null);
    setMode('create'); setManualCreate(false); setVariation(null); setError(null); setSaveMessage(null);
    setAuthoringSession((current) => current + 1);
  }

  async function onDelete(scenario: ScenarioRecord) {
    if (!window.confirm(`Delete “${scenario.title}”? This cannot be undone.`)) return;
    setIsDeleting(true);
    setError(null);
    setSaveMessage(null);
    try {
      await deleteScenario(scenario.id);
      setSelectedKey('');
      setSaveMessage('Scenario deleted.');
      setReloadKey((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete scenario');
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <section className="scenarios-shell" aria-label="Scenarios">
      <div className="scenarios-layout">
        <aside id="scenario-catalog" className="scenarios-sidebar card">
          <div className="scenarios-sidebar-header">
            <div>
              <p className="eyebrow">Evaluation suites</p>
              <h2>Test library</h2>
              <ApiAwareLink className="primary-link" href="/specs/new">Create test set</ApiAwareLink>
            </div>
            <button type="button" className="primary-link" onClick={beginQuickCase}>
              Add quick case
            </button>
          </div>

          {isLoading ? <p className="scenarios-muted">Loading scenarios…</p> : null}
          {!isLoading && error ? (
            <div className="scenarios-error" role="alert">
              <p style={{ margin: 0 }}>{error}</p>
              <button type="button" className="secondary-link" style={{ marginTop: 10 }} onClick={() => setReloadKey((value) => value + 1)}>
                Retry loading scenarios
              </button>
            </div>
          ) : null}

          {!isLoading && !error ? suites.map((suite) => (
            <section className="scenarios-suite-group" key={suite.id} aria-labelledby={`suite-${suite.id}`}>
              <div>
                <h3 id={`suite-${suite.id}`}>{suite.title}</h3>
                <p>{suite.scenarios.length} scenario{suite.scenarios.length === 1 ? '' : 's'}</p>
              </div>
              <ul className="scenarios-list">
                {suite.scenarios.map((scenario) => {
                  const key = scenarioKey(suite.id, scenario.id);
                  return (
                    <li key={key}>
                      <button
                        type="button"
                        className={selectedKey === key && mode === 'view' ? 'is-active' : undefined}
                        onClick={() => {
                          setSelectedKey(key);
                          setMode('view');
                          setSaveMessage(null);
                          if (window.matchMedia('(max-width: 900px)').matches) {
                            window.requestAnimationFrame(() => detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
                          }
                        }}
                      >
                        <strong>{scenario.title}</strong>
                        <span>{scenario.user_goal || scenario.description || scenario.user_persona}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          )) : null}
        </aside>

        <div className="scenarios-main">
          {saveMessage ? <p className="scenarios-muted" role="status">{saveMessage}</p> : null}
          {copyMessage ? <p className="scenarios-muted" role="status">{copyMessage}</p> : null}

          {mode === 'create' && !manualCreate ? <AssistedTestBuilder key={authoringSession} scope="case"
            context={variation || undefined} initialRequest={variation ? 'Make a variation of this case where the caller changes their mind. Keep the same policy boundaries.' : ''}
            onApprove={createAssistedCase} onManual={editAssistedCase} onCancel={() => setMode('view')} /> : null}
          {mode === 'create' && manualCreate ? (
            <form className="card scenarios-create-form" onSubmit={(event) => void onCreate(event)}>
              <div>
                <p className="eyebrow">User Scenarios</p>
                <h2>Add a quick case</h2>
                <p className="scenarios-muted">Detailed editor · explicit rules. For versioned policies and multiple cases, <ApiAwareLink href="/specs/new">create a test set</ApiAwareLink>.</p>
                <button type="button" className="secondary-link" onClick={() => setManualCreate(false)}>Back to assisted drafting</button>
              </div>
              {error ? <div className="scenarios-error" role="alert">{error}</div> : null}
              {manualContext ? <details><summary>Retained draft policy and evidence requirements</summary>
                <p>{manualContext.permissible_behavior}</p><p>Required evidence: {manualContext.evidence_requirements.join(', ')}</p><p>{manualContext.requirements}</p>
              </details> : null}
              <label htmlFor="scenario-title"><span>Title (optional)</span><input id="scenario-title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Account lockout handoff" /></label>
              <label htmlFor="scenario-prompt"><span>{manualContext?.caller_steps ? 'Caller profile / background' : 'User prompt / persona'}</span><textarea id="scenario-prompt" required={!manualContext?.caller_steps} rows={3} value={simulatedUserPrompt} onChange={(event) => setSimulatedUserPrompt(event.target.value)} placeholder="Describe the user’s situation and request…" /></label>
              {manualContext?.caller_steps ? <label htmlFor="scenario-caller-steps"><span>Caller instructions (first line is spoken)</span>
                <textarea id="scenario-caller-steps" aria-label="Caller instructions (first line is spoken)" aria-describedby="scenario-caller-help" required rows={4} value={manualContext.caller_steps.join('\n')}
                  onChange={(event) => setManualContext((current) => current ? { ...current, caller_steps: event.target.value.split('\n') } : null)} />
                <small id="scenario-caller-help">The first line is the actual opening. Background edits do not replace these instructions.</small>
              </label> : null}
              {!manualContext?.caller_steps ? <label htmlFor="scenario-opener"><span>Spoken opening line</span><textarea id="scenario-opener" required maxLength={4000} rows={3} value={spokenOpener} onChange={(event) => setSpokenOpener(event.target.value)} placeholder="Hi, can you tell me about your wireless headphones?" /><small className="scenarios-muted">The tester says this line exactly. Keep persona, instructions and expected results in their separate fields.</small></label> : null}
              <label htmlFor="scenario-expected"><span>Expected outcome</span><textarea id="scenario-expected" required rows={5} value={expectedOutput} onChange={(event) => setExpectedOutput(event.target.value)} placeholder="What the agent should do or say…" /></label>
              <label htmlFor="scenario-required"><span>Required behaviors (one per line)</span><textarea id="scenario-required" required rows={4} value={requiredActions} onChange={(event) => setRequiredActions(event.target.value)} placeholder="Offer housing options within the caller’s budget" /></label>
              <label htmlFor="scenario-forbidden"><span>Forbidden behaviors (one per line, optional)</span><textarea id="scenario-forbidden" rows={4} value={forbiddenActions} onChange={(event) => setForbiddenActions(event.target.value)} placeholder="Execute a house sale\nHandle payments" /></label>
              <label htmlFor="scenario-description"><span>Description</span><textarea id="scenario-description" required rows={5} value={description} onChange={(event) => { setMirrorPrompt(false); setDescription(event.target.value); }} placeholder="Often mirrors the user prompt…" /></label>
              <label className="scenarios-checkbox" htmlFor="scenario-mirror"><input id="scenario-mirror" type="checkbox" checked={mirrorPrompt} onChange={(event) => setMirrorPrompt(event.target.checked)} />Keep description mirrored from the user prompt</label>
              <div className="scenarios-actions">
                <button type="submit" className="primary-link" disabled={isSaving}>{isSaving ? 'Creating…' : 'Create scenario'}</button>
                <button type="button" className="secondary-link" onClick={() => setMode('view')}>Cancel</button>
              </div>
            </form>
          ) : null}

          {mode === 'view' && selected ? (
            <section ref={detailRef} id="scenario-detail" className="card scenarios-detail" aria-label="Selected scenario">
              <div className="scenarios-detail-header">
                <div>
                  <p className="eyebrow">{selected.suite.title}</p>
                  <h2>{selected.scenario.title}</h2>
                  <p className="scenarios-muted">{selected.suite.description}</p>
                </div>
                <div className="scenarios-actions">
                  <button type="button" className="secondary-link" onClick={() => {
                    beginQuickCase();
                    setVariation({ label: selected.scenario.title, text: [
                      `Caller profile: ${selected.scenario.user_persona}`,
                      `Opening: ${selected.scenario.caller_steps[0] || selected.scenario.spoken_opener || selected.scenario.simulated_user_prompt}`,
                      `Complete caller instructions:\n${selected.scenario.caller_steps.join('\n')}`,
                      `Expected outcome: ${selected.scenario.expected_output}`,
                      `Required: ${selected.scenario.required_actions.join('; ')}`,
                      `Forbidden: ${selected.scenario.forbidden_actions.join('; ')}`,
                      `Allowed assistance: ${selected.scenario.permissible_behavior}`,
                      `Policy context: ${selected.scenario.policy_context || selected.scenario.requirements}`,
                    ].join('\n') }); setManualCreate(false); setMode('create'); setError(null);
                  }}>Make a variation</button>
                  <Link className="primary-link" href={scenarioHref('/runs', selected.scenario)}>Test an agent</Link>
                  <Link className="secondary-link" href={scenarioHref('/eval', selected.scenario)}>Analyze a conversation</Link>
                  {selected.scenario.suite_id === 'user-scenarios' ? (
                    <button
                      type="button"
                      className="secondary-link scenario-delete-button"
                      disabled={isDeleting}
                      onClick={() => void onDelete(selected.scenario)}
                    >
                      {isDeleting ? 'Deleting…' : 'Delete scenario'}
                    </button>
                  ) : null}
                </div>
              </div>
              <a className="scenario-back-link" href="#scenario-catalog">← Back to scenario catalog</a>
              <FieldCard title="User persona / starting prompt" value={selected.scenario.user_persona || selected.scenario.simulated_user_prompt} onCopy={(label, text) => void onCopy(label, text)} />
              <FieldCard title="Spoken opening line" value={selected.scenario.caller_steps[0] || selected.scenario.spoken_opener || 'Not explicitly set. A direct caller request or neutral greeting is used; persona instructions are not spoken.'} onCopy={(label, text) => void onCopy(label, text)} />
              <FieldCard title="Goal" value={selected.scenario.user_goal || selected.scenario.description} onCopy={(label, text) => void onCopy(label, text)} />
              {selected.scenario.target_behavior_id ? <p className="scenarios-muted">This case exercises behavior {selected.scenario.target_behavior_id} only. Other rules remain policy context, not measured coverage.</p> : null}
              <FieldCard title="Required actions" value={selected.scenario.target_behavior_id && !selected.scenario.required_actions.length ? 'n/a — not this case’s focus.' : listText(selected.scenario.required_actions)} onCopy={(label, text) => void onCopy(label, text)} />
              <FieldCard title="Forbidden behaviors" value={selected.scenario.target_behavior_id && !selected.scenario.forbidden_actions.length ? 'n/a — not this case’s focus.' : listText(selected.scenario.forbidden_actions)} onCopy={(label, text) => void onCopy(label, text)} />
              {selected.scenario.policy_context ? <FieldCard title="Complete policy context" value={selected.scenario.policy_context} onCopy={(label, text) => void onCopy(label, text)} /> : null}
              <FieldCard title="Expected final state" value={selected.scenario.expected_final_state || selected.scenario.expected_output} onCopy={(label, text) => void onCopy(label, text)} />
              {selected.scenario.evaluation_spec_ref ? <FieldCard title="Evaluation design version" value={selected.scenario.evaluation_spec_ref} onCopy={(label, text) => void onCopy(label, text)} /> : null}
              {selected.scenario.caller_steps.length ? <FieldCard title="Caller-side instructions" value={selected.scenario.caller_steps.join('\n')} onCopy={(label, text) => void onCopy(label, text)} /> : null}
              {selected.scenario.permissible_behavior ? <FieldCard title="Allowed assistance" value={selected.scenario.permissible_behavior} onCopy={(label, text) => void onCopy(label, text)} /> : null}
              <FieldCard title="Evidence requirements" value={selected.scenario.evidence_requirements.length
                ? listText(selected.scenario.evidence_requirements.map((item) => item.replaceAll('_', ' ')))
                : '• Conversation transcript or vCon\n• Action/tool trace\n• Final state'} onCopy={(label, text) => void onCopy(label, text)} />
            </section>
          ) : null}

          {mode === 'view' && !selected && !isLoading ? (
            <section className="card scenarios-empty"><h2>No scenarios available</h2><p className="scenarios-muted">Create a scenario or check the API connection.</p></section>
          ) : null}
        </div>
      </div>
    </section>
  );
}
