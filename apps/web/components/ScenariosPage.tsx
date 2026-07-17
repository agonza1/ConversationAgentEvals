'use client';

import Link from 'next/link';
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
  expected_output: string;
  constraints: string[];
  required_actions: string[];
  forbidden_actions: string[];
  expected_final_state: string;
  source: string;
}

interface ScenarioSuite {
  id: string;
  title: string;
  description: string;
  scenarios: ScenarioRecord[];
}

function getApiBase() {
  if (typeof window === 'undefined') {
    return (process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8025')
      .replace(/\/$/, '')
      .replace(/\/api$/, '');
  }

  const fromQuery = new URLSearchParams(window.location.search).get('api_base');
  if (fromQuery) {
    try {
      return new URL(fromQuery, window.location.origin).toString().replace(/\/$/, '').replace(/\/api$/, '');
    } catch {
      // Ignore malformed overrides.
    }
  }
  return '';
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
    expected_output: displayValue(record.expected_output ?? record.expected_final_state),
    constraints: stringList(record.constraints),
    required_actions: stringList(record.required_actions),
    forbidden_actions: stringList(record.forbidden_actions),
    expected_final_state: displayValue(record.expected_final_state ?? record.expected_output),
    source: String(record.source ?? ''),
  };
}

function normalizeSuites(value: unknown): ScenarioSuite[] {
  const record = asRecord(value);
  const rawSuites = Array.isArray(value) ? value : Array.isArray(record.suites) ? record.suites : [];
  return rawSuites.map((item) => {
    const suite = asRecord(item);
    const id = String(suite.id ?? suite.suite_id ?? '');
    return {
      id,
      title: String(suite.title ?? suite.name ?? 'Untitled suite'),
      description: String(suite.description ?? ''),
      scenarios: (Array.isArray(suite.scenarios) ? suite.scenarios : []).map((scenario) => normalizeScenario(scenario, id)),
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
  expected_output: string;
  description: string;
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
  const [title, setTitle] = useState('');
  const [simulatedUserPrompt, setSimulatedUserPrompt] = useState('');
  const [expectedOutput, setExpectedOutput] = useState('');
  const [description, setDescription] = useState('');
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
        const querySelection = params.get('suite_id') && params.get('scenario_id')
          ? scenarioKey(String(params.get('suite_id')), String(params.get('scenario_id')))
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
        simulated_user_prompt: simulatedUserPrompt.trim(),
        expected_output: expectedOutput.trim(),
        description: description.trim(),
      });
      preferredSelectionRef.current = scenarioKey('user-scenarios', created.id);
      setMode('view');
      setSaveMessage('Scenario created in the User Scenarios evaluation suite.');
      setTitle('');
      setSimulatedUserPrompt('');
      setExpectedOutput('');
      setDescription('');
      setMirrorPrompt(true);
      setReloadKey((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create scenario');
    } finally {
      setIsSaving(false);
    }
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
              <h2>Scenario catalog</h2>
            </div>
            <button type="button" className="primary-link" onClick={() => { setMode('create'); setError(null); setSaveMessage(null); }}>
              Create scenario
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

          {mode === 'create' ? (
            <form className="card scenarios-create-form" onSubmit={(event) => void onCreate(event)}>
              <div>
                <p className="eyebrow">User Scenarios</p>
                <h2>Create a scenario</h2>
                <p className="scenarios-muted">Describe the user request and the outcome the agent must achieve.</p>
              </div>
              {error ? <div className="scenarios-error" role="alert">{error}</div> : null}
              <label htmlFor="scenario-title"><span>Title (optional)</span><input id="scenario-title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Account lockout handoff" /></label>
              <label htmlFor="scenario-prompt"><span>User prompt / persona</span><textarea id="scenario-prompt" required rows={6} value={simulatedUserPrompt} onChange={(event) => setSimulatedUserPrompt(event.target.value)} placeholder="Describe the user’s situation and request…" /></label>
              <label htmlFor="scenario-expected"><span>Expected outcome</span><textarea id="scenario-expected" required rows={5} value={expectedOutput} onChange={(event) => setExpectedOutput(event.target.value)} placeholder="What the agent should do or say…" /></label>
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
                  <Link className="primary-link" href={scenarioHref('/runs', selected.scenario)}>Run agent</Link>
                  <Link className="secondary-link" href={scenarioHref('/eval', selected.scenario)}>Eval sample evidence</Link>
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
              <FieldCard title="Goal" value={selected.scenario.user_goal || selected.scenario.description} onCopy={(label, text) => void onCopy(label, text)} />
              <FieldCard title="Required actions" value={listText(selected.scenario.required_actions)} onCopy={(label, text) => void onCopy(label, text)} />
              <FieldCard title="Forbidden behaviors" value={listText(selected.scenario.forbidden_actions)} onCopy={(label, text) => void onCopy(label, text)} />
              <FieldCard title="Expected final state" value={selected.scenario.expected_final_state || selected.scenario.expected_output} onCopy={(label, text) => void onCopy(label, text)} />
              <FieldCard title="Evidence requirements" value={'• Conversation transcript or vCon\n• Action/tool trace\n• Final state'} onCopy={(label, text) => void onCopy(label, text)} />
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
