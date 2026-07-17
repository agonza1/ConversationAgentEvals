'use client';

import { useEffect, useMemo, useState } from 'react';

type VoiceMode = 'voice_fixture' | 'pipecat_webrtc';

type JsonRecord = Record<string, unknown>;

interface ExecutionConversation {
  conversation_id: string;
  scenario_id: string;
  scenario_title?: string | null;
  mode: VoiceMode | string;
  status: string;
  turns?: Array<{ speaker?: string | null; text?: string | null }>;
  transcript?: string | null;
  recording?: JsonRecord | null;
  vcon_export_summary?: JsonRecord | null;
  audio_session?: JsonRecord | null;
  verdict?: string | null;
  score?: number | null;
  error?: string | null;
}

interface ExecutionRun {
  execution_run_id: string;
  status: string;
  progress: {
    completed_conversations: number;
    total_conversations: number;
    percent: number;
  };
  conversations: ExecutionConversation[];
}

function normalizeApiBase(value: string) {
  return value.replace(/\/$/, '').replace(/\/api$/, '');
}

function getApiBase() {
  if (typeof window === 'undefined') {
    return normalizeApiBase(process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8025');
  }
  const fromQuery = new URLSearchParams(window.location.search).get('api_base');
  if (fromQuery) {
    try {
      return normalizeApiBase(new URL(fromQuery, window.location.origin).toString());
    } catch {
      // fall through
    }
  }
  return '';
}

async function handleJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      throw new Error(parsed.detail || text || `Request failed (${response.status})`);
    } catch (error) {
      if (error instanceof Error && error.message !== text) throw error;
      throw new Error(text || `Request failed (${response.status})`);
    }
  }
  return response.json() as Promise<T>;
}

function ensureDemoIdentity() {
  const userId = window.localStorage.getItem('conversation-evals-demo-user') || `voice-user-${Date.now()}`;
  const projectId = window.localStorage.getItem('conversation-evals-demo-project') || 'conversation-agent-evals';
  window.localStorage.setItem('conversation-evals-demo-user', userId);
  window.localStorage.setItem('conversation-evals-demo-project', projectId);
  return { userId, projectId };
}

function statusColor(status?: string | null) {
  if (status === 'completed' || status === 'pass') return 'var(--success-text)';
  if (status === 'failed' || status === 'fail') return 'var(--error-text)';
  if (status === 'needs_review' || status === 'running' || status === 'queued') return 'var(--warn-text, #9a6700)';
  return 'var(--muted)';
}

function evidenceLine(conversation: ExecutionConversation) {
  const parts: string[] = [];
  if (conversation.mode === 'pipecat_webrtc') parts.push('Pipecat hooks');
  const url = conversation.recording?.recording_url ?? conversation.recording?.uri;
  if (typeof url === 'string' && url.trim()) parts.push('recording');
  const summary = conversation.vcon_export_summary;
  if (typeof summary?.dialog_turns === 'number') parts.push(`vCon · ${summary.dialog_turns} turns`);
  else if (summary) parts.push('vCon');
  const session = conversation.audio_session;
  if (typeof session?.frames_sent === 'number' || typeof session?.frames_received === 'number') {
    parts.push(`frames ${session.frames_sent ?? 0}/${session.frames_received ?? 0}`);
  }
  return parts.length ? parts.join(' · ') : null;
}

const modeOptions = [
  {
    id: 'pipecat_webrtc',
    label: 'Pipecat hooks',
    eyebrow: 'Integration path',
    description: 'Exercise in-process audio send/receive hooks and capture transport evidence.',
    detail: 'Includes vCon + frame counts',
  },
  {
    id: 'voice_fixture',
    label: 'Fixture',
    eyebrow: 'Fast smoke test',
    description: 'Run the same scoring workflow against deterministic offline audio.',
    detail: 'No live transport required',
  },
] as const;

export function VoiceEvalPage() {
  const [mode, setMode] = useState<VoiceMode>('pipecat_webrtc');
  const [error, setError] = useState<string | null>(null);
  const [isLaunching, setIsLaunching] = useState(false);
  const [run, setRun] = useState<ExecutionRun | null>(null);

  const active = useMemo(
    () => run?.status === 'queued' || run?.status === 'running',
    [run?.status],
  );

  useEffect(() => {
    if (!run || !active) return;
    const identity = ensureDemoIdentity();
    const timer = window.setInterval(() => {
      fetch(
        `${getApiBase()}/api/execution/runs/${encodeURIComponent(run.execution_run_id)}?user_id=${encodeURIComponent(identity.userId)}`,
        { cache: 'no-store' },
      )
        .then((response) => handleJson<ExecutionRun>(response))
        .then(setRun)
        .catch(() => undefined);
    }, 800);
    return () => window.clearInterval(timer);
  }, [run, active]);

  async function onLaunch() {
    const identity = ensureDemoIdentity();
    setIsLaunching(true);
    setError(null);
    try {
      const queued = await handleJson<ExecutionRun>(
        await fetch(`${getApiBase()}/api/execution/runs`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            suite_id: 'call-center-voice-ai',
            scenario_ids: ['cancellation-rescue'],
            mode,
            iterations: 1,
            user_id: identity.userId,
            project_id: identity.projectId,
            evaluate: true,
            audio_transport: mode === 'pipecat_webrtc' ? 'pipecat_small_webrtc' : 'none',
          }),
        }),
      );
      setRun(queued);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Launch failed.');
    } finally {
      setIsLaunching(false);
    }
  }

  const selectedMode = modeOptions.find((option) => option.id === mode) ?? modeOptions[0];

  return (
    <section className="voice-eval-workspace" aria-label="Voice evaluation">
      <aside className="voice-scenario-card" aria-labelledby="voice-scenario-title">
        <div className="voice-section-heading">
          <span className="voice-step">Scenario</span>
          <p className="eyebrow">Cancellation rescue</p>
          <h2 id="voice-scenario-title">Can the agent retain an at-risk caller?</h2>
          <p>
            A caller asks to cancel. The agent should understand the reason, respond appropriately,
            and preserve a clear evidence trail without inventing actions.
          </p>
        </div>
        <dl className="voice-scenario-facts">
          <div><dt>Suite</dt><dd>Call center voice AI</dd></div>
          <div><dt>Iterations</dt><dd>1 conversation</dd></div>
          <div><dt>Evaluation</dt><dd>Automatic scoring</dd></div>
        </dl>
        <div className="voice-evidence-list" aria-label="Expected evidence">
          <strong>Evidence captured</strong>
          <span>Transcript</span>
          <span>Recording metadata</span>
          <span>vCon export</span>
          <span>Transport frames</span>
        </div>
      </aside>

      <div className="voice-eval-main">
        <section className="card voice-mode-panel" aria-labelledby="voice-mode-title">
          <div className="voice-panel-heading">
            <div>
              <span className="voice-step">1</span>
              <p className="eyebrow">Choose a test path</p>
              <h2 id="voice-mode-title">How should this call run?</h2>
            </div>
            <span className="voice-selection-summary">Selected · {selectedMode.label}</span>
          </div>

          <div role="group" aria-label="Voice evaluation mode" className="voice-mode-grid">
            {modeOptions.map((option) => {
              const selected = mode === option.id;
              return (
                <button
                  key={option.id}
                  type="button"
                  className="voice-mode-option"
                  data-selected={selected}
                  aria-label={option.label}
                  aria-pressed={selected}
                  onClick={() => setMode(option.id)}
                >
                  <span className="voice-mode-radio" aria-hidden="true">{selected ? '✓' : ''}</span>
                  <span className="voice-mode-copy">
                    <small>{option.eyebrow}</small>
                    <strong>{option.label}</strong>
                    <span>{option.description}</span>
                    <em>{option.detail}</em>
                  </span>
                </button>
              );
            })}
          </div>

          {error ? (
            <div className="voice-error" role="alert">
              <strong>Could not start the evaluation</strong>
              <span>{error}</span>
            </div>
          ) : null}

          <div className="voice-launch-bar">
            <div>
              <span className="voice-step">2</span>
              <strong>Run cancellation rescue</strong>
              <small>{selectedMode.detail}</small>
            </div>
            <button
              type="button"
              className="voice-run-button"
              aria-label="Run"
              onClick={onLaunch}
              disabled={isLaunching || active}
            >
              {isLaunching ? 'Starting…' : active ? 'Running…' : 'Run voice eval'}
              {!isLaunching && !active ? <span aria-hidden="true">→</span> : null}
            </button>
          </div>
        </section>

        <section className="card voice-results-panel" aria-labelledby="voice-results-title" aria-live="polite">
          <div className="voice-panel-heading">
            <div>
              <span className="voice-step">3</span>
              <p className="eyebrow">Evidence and score</p>
              <h2 id="voice-results-title">Run results</h2>
            </div>
            {run ? (
              <span className="voice-run-status" data-status={run.status}>
                <i aria-hidden="true" />{run.status}
              </span>
            ) : null}
          </div>

          {run ? (
            <div className="voice-results-content">
              <div className="voice-run-meta">
                <div>
                  <span>Run ID</span>
                  <strong>{run.execution_run_id}</strong>
                </div>
                <div>
                  <span>Progress</span>
                  <strong>{run.progress.completed_conversations}/{run.progress.total_conversations} conversations</strong>
                </div>
              </div>
              <div
                className="voice-progress-track"
                role="progressbar"
                aria-label="Voice evaluation progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.max(0, Math.min(100, run.progress.percent))}
                aria-valuetext={`${run.progress.completed_conversations} of ${run.progress.total_conversations} conversations complete`}
              >
                <span style={{ width: `${Math.max(0, Math.min(100, run.progress.percent))}%` }} />
              </div>

              <div aria-label="Voice eval conversations" className="voice-conversation-list">
                {(run.conversations || []).length ? (
                  [...run.conversations].reverse().map((conversation) => {
                    const evidence = evidenceLine(conversation);
                    const outcome = conversation.verdict || conversation.status;
                    return (
                      <article key={conversation.conversation_id} className="voice-conversation-card">
                        <div className="voice-conversation-header">
                          <div>
                            <p className="eyebrow">Evaluated conversation</p>
                            <h3>{conversation.scenario_title || conversation.scenario_id}</h3>
                          </div>
                          <div className="voice-score" style={{ color: statusColor(outcome) }}>
                            <strong>{typeof conversation.score === 'number' ? conversation.score : '—'}</strong>
                            <span>{outcome}</span>
                          </div>
                        </div>
                        {evidence ? (
                          <div className="voice-evidence-chips">
                            {evidence.split(' · ').map((item) => <span key={item}>{item}</span>)}
                          </div>
                        ) : null}
                        {conversation.error ? <div className="voice-error" role="alert">{conversation.error}</div> : null}
                        {(conversation.turns || []).length ? (
                          <div className="voice-transcript" aria-label="Conversation transcript">
                            {(conversation.turns || []).map((turn, index) => (
                              <div key={`${turn.speaker || 'turn'}-${index}`}>
                                <strong>{turn.speaker || 'Speaker'}</strong>
                                <p>{turn.text || '—'}</p>
                              </div>
                            ))}
                          </div>
                        ) : conversation.transcript ? (
                          <pre className="voice-transcript-raw">{conversation.transcript}</pre>
                        ) : null}
                      </article>
                    );
                  })
                ) : (
                  <div className="voice-waiting-state" aria-live="polite">
                    <span className="voice-pulse" aria-hidden="true" />
                    <div><strong>Evaluation in progress</strong><p>Waiting for the first conversation evidence…</p></div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="voice-empty-results">
              <div className="voice-empty-icon" aria-hidden="true">◎</div>
              <div>
                <strong>Your evidence will appear here</strong>
                <p>Choose a test path and run the scenario to see the score, transcript, and captured artifacts.</p>
              </div>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}
