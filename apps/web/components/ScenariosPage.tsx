'use client';

import Link from 'next/link';
import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

type JsonRecord = Record<string, unknown>;

interface ScenarioRecord {
  id: string;
  suite_id: string;
  title: string;
  type: 'scenario';
  description: string;
  simulated_user_prompt: string;
  expected_output: string;
  created_at?: string | null;
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
      // Ignore malformed override.
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
      if (typeof parsed?.detail === 'string') message = parsed.detail;
    } catch {
      // Keep plain text.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function normalizeScenario(value: unknown): ScenarioRecord {
  const record = (value && typeof value === 'object' ? value : {}) as JsonRecord;
  return {
    id: String(record.id ?? ''),
    suite_id: String(record.suite_id ?? 'user-scenarios'),
    title: String(record.title ?? 'Untitled scenario'),
    type: 'scenario',
    description: String(record.description ?? ''),
    simulated_user_prompt: String(record.simulated_user_prompt ?? record.prompt ?? ''),
    expected_output: String(record.expected_output ?? record.expected_final_state ?? ''),
    created_at: typeof record.created_at === 'string' ? record.created_at : null,
  };
}

async function listScenarios(signal?: AbortSignal): Promise<ScenarioRecord[]> {
  const payload = await handleJson<{ scenarios?: unknown[] }>(
    await fetch(`${getApiBase()}/api/scenarios`, { cache: 'no-store', signal }),
  );
  return (payload.scenarios ?? []).map(normalizeScenario);
}

async function createScenario(payload: {
  title?: string;
  simulated_user_prompt: string;
  expected_output: string;
  description: string;
}): Promise<ScenarioRecord> {
  return normalizeScenario(
    await handleJson(
      await fetch(`${getApiBase()}/api/scenarios`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }),
    ),
  );
}

async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Fall through.
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

function ScenarioTypeBadge() {
  return (
    <div className="scenario-type-block">
      <p className="scenario-type-label">Type</p>
      <span className="scenario-type-badge" aria-label="Type Scenario">
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="7" fill="currentColor" opacity="0.18" />
          <path d="M6.2 4.8v6.4L11.4 8 6.2 4.8z" fill="currentColor" />
        </svg>
        Scenario
      </span>
    </div>
  );
}

function FieldCard({
  title,
  value,
  onCopy,
}: {
  title: string;
  value: string;
  onCopy: (label: string, text: string) => void;
}) {
  return (
    <article className="scenario-field-card">
      <div className="scenario-field-card-header">
        <h3>{title}</h3>
        <button
          type="button"
          className="scenario-copy-button"
          aria-label={`Copy ${title}`}
          onClick={() => onCopy(title, value)}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="5" y="5" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <rect x="3" y="3" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
          </svg>
        </button>
      </div>
      <p>{value || '—'}</p>
    </article>
  );
}

export function ScenariosPage() {
  const [scenarios, setScenarios] = useState<ScenarioRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [mode, setMode] = useState<'list' | 'create' | 'view'>('list');
  const [title, setTitle] = useState('');
  const [simulatedUserPrompt, setSimulatedUserPrompt] = useState('');
  const [expectedOutput, setExpectedOutput] = useState('');
  const [description, setDescription] = useState('');
  const [mirrorPrompt, setMirrorPrompt] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const loadRequestRef = useRef(0);

  const selected = useMemo(
    () => scenarios.find((item) => item.id === selectedId) ?? null,
    [scenarios, selectedId],
  );

  useEffect(() => {
    const requestId = loadRequestRef.current + 1;
    loadRequestRef.current = requestId;
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timeoutId = window.setTimeout(() => controller?.abort(), 12000);

    async function load() {
      setIsLoading(true);
      setError(null);
      try {
        const next = await listScenarios(controller?.signal);
        if (loadRequestRef.current !== requestId) return;
        setScenarios(next);
        if (next[0]) setSelectedId(next[0].id);
        setMode(next[0] ? 'view' : 'list');
      } catch (err) {
        if (loadRequestRef.current !== requestId) return;
        setScenarios([]);
        const aborted = typeof err === 'object' && err !== null && 'name' in err && (err as { name?: string }).name === 'AbortError';
        setError(
          aborted
            ? 'Timed out loading scenarios. Check that the API is running and reachable.'
            : err instanceof Error
              ? err.message
              : 'Could not load scenarios',
        );
      } finally {
        window.clearTimeout(timeoutId);
        if (loadRequestRef.current === requestId) {
          setIsLoading(false);
        }
      }
    }

    void load();
    return () => {
      controller?.abort();
      window.clearTimeout(timeoutId);
      if (loadRequestRef.current === requestId) {
        loadRequestRef.current = requestId + 1;
      }
    };
  }, [reloadKey]);

  useEffect(() => {
    if (mirrorPrompt && mode === 'create') {
      setDescription(simulatedUserPrompt);
    }
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
      setScenarios((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setSelectedId(created.id);
      setMode('view');
      setSaveMessage('Scenario created. It is selectable from the User Scenarios suite in the benchmark runner.');
      setTitle('');
      setSimulatedUserPrompt('');
      setExpectedOutput('');
      setDescription('');
      setMirrorPrompt(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create scenario');
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <section className="scenarios-shell" aria-label="Scenarios">
      <div className="scenarios-layout">
        <aside className="scenarios-sidebar card">
          <div className="scenarios-sidebar-header">
            <div>
              <p className="eyebrow">Scenarios</p>
              <h2>Your scenario library</h2>
            </div>
            <button
              type="button"
              className="primary-link"
              onClick={() => {
                setMode('create');
                setSaveMessage(null);
                setError(null);
              }}
            >
              Create scenario
            </button>
          </div>

          {isLoading ? <p className="scenarios-muted">Loading scenarios…</p> : null}
          {!isLoading && error ? (
            <div className="scenarios-error" role="alert">
              <p style={{ margin: 0 }}>{error}</p>
              <button
                type="button"
                className="secondary-link"
                style={{ marginTop: 10 }}
                onClick={() => setReloadKey((value) => value + 1)}
              >
                Retry loading scenarios
              </button>
            </div>
          ) : null}
          {!isLoading && !error && !scenarios.length ? (
            <p className="scenarios-muted">No custom scenarios yet. Create one to see it here and in the runner.</p>
          ) : null}

          <ul className="scenarios-list">
            {scenarios.map((scenario) => (
              <li key={scenario.id}>
                <button
                  type="button"
                  className={selectedId === scenario.id && mode !== 'create' ? 'is-active' : undefined}
                  onClick={() => {
                    setSelectedId(scenario.id);
                    setMode('view');
                    setSaveMessage(null);
                  }}
                >
                  <strong>{scenario.title}</strong>
                  <span>{scenario.description.slice(0, 120) || scenario.simulated_user_prompt.slice(0, 120)}</span>
                </button>
              </li>
            ))}
          </ul>

          <p className="scenarios-muted scenarios-note">
            Persistence: file-backed JSON under <code>storage/user_scenarios.json</code>, registered into the
            existing benchmark catalog as suite <strong>User Scenarios</strong>.
          </p>
        </aside>

        <div className="scenarios-main">
          {error && mode === 'create' ? <div className="scenarios-error" role="alert">{error}</div> : null}
          {saveMessage ? <p className="scenarios-muted">{saveMessage}</p> : null}
          {copyMessage ? <p className="scenarios-muted" role="status">{copyMessage}</p> : null}

          {mode === 'create' ? (
            <form className="card scenarios-create-form" onSubmit={(event) => void onCreate(event)}>
              <div>
                <p className="eyebrow">Create</p>
                <h2>New scenario</h2>
                <p className="scenarios-muted">
                  Match the scenario detail fields: simulated user prompt, expected output, and description.
                </p>
              </div>

              <ScenarioTypeBadge />

              <label htmlFor="scenario-title">
                <span>Title (optional)</span>
                <input
                  id="scenario-title"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="Account lockout handoff"
                />
              </label>

              <label htmlFor="scenario-prompt">
                <span>Simulated User Prompt</span>
                <textarea
                  id="scenario-prompt"
                  required
                  rows={6}
                  value={simulatedUserPrompt}
                  onChange={(event) => setSimulatedUserPrompt(event.target.value)}
                  placeholder="Describe the user’s situation and request…"
                />
              </label>

              <label htmlFor="scenario-expected">
                <span>Expected Output</span>
                <textarea
                  id="scenario-expected"
                  required
                  rows={5}
                  value={expectedOutput}
                  onChange={(event) => setExpectedOutput(event.target.value)}
                  placeholder="What the agent should do or say…"
                />
              </label>

              <label htmlFor="scenario-description">
                <span>Description</span>
                <textarea
                  id="scenario-description"
                  required
                  rows={5}
                  value={description}
                  onChange={(event) => {
                    setMirrorPrompt(false);
                    setDescription(event.target.value);
                  }}
                  placeholder="Often mirrors the simulated user prompt…"
                />
              </label>

              <label className="scenarios-checkbox" htmlFor="scenario-mirror">
                <input
                  id="scenario-mirror"
                  type="checkbox"
                  checked={mirrorPrompt}
                  onChange={(event) => setMirrorPrompt(event.target.checked)}
                />
                Keep description mirrored from the simulated user prompt
              </label>

              <div className="scenarios-actions">
                <button type="submit" className="primary-link" disabled={isSaving}>
                  {isSaving ? 'Creating…' : 'Create scenario'}
                </button>
                <button
                  type="button"
                  className="secondary-link"
                  onClick={() => setMode(selected ? 'view' : 'list')}
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : null}

          {mode !== 'create' && selected ? (
            <section className="card scenarios-detail" aria-label="Selected scenario">
              <div className="scenarios-detail-header">
                <div>
                  <p className="eyebrow">View</p>
                  <h2>{selected.title}</h2>
                  <p className="scenarios-muted">
                    Suite <code>{selected.suite_id}</code> · selectable in{' '}
                    <Link href="/benchmarks">benchmark runner</Link>
                  </p>
                </div>
                <button type="button" className="secondary-link" onClick={() => setMode('create')}>
                  Create another
                </button>
              </div>

              <ScenarioTypeBadge />
              <FieldCard
                title="Simulated User Prompt"
                value={selected.simulated_user_prompt}
                onCopy={(label, text) => void onCopy(label, text)}
              />
              <FieldCard
                title="Expected Output"
                value={selected.expected_output}
                onCopy={(label, text) => void onCopy(label, text)}
              />
              <FieldCard
                title="Description"
                value={selected.description}
                onCopy={(label, text) => void onCopy(label, text)}
              />
            </section>
          ) : null}

          {mode !== 'create' && !selected && !isLoading ? (
            <section className="card scenarios-empty">
              <ScenarioTypeBadge />
              <h2>Create your first scenario</h2>
              <p className="scenarios-muted">
                Capture a simulated user prompt, expected agent output, and description — then run it from the
                User Scenarios suite.
              </p>
              <button type="button" className="primary-link" onClick={() => setMode('create')}>
                Create scenario
              </button>
            </section>
          ) : null}
        </div>
      </div>
    </section>
  );
}
