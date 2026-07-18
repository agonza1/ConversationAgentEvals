'use client';

import { useEffect, useMemo, useState } from 'react';

import { ApiAwareLink } from './ApiAwareLink';

type VoiceMode = 'pipecat_webrtc';
type JsonRecord = Record<string, unknown>;

interface AgentRecord {
  id: string;
  name: string;
  channel: 'text' | 'voice';
  target: string;
  description?: string | null;
}

interface AudioTransportCapability {
  id: string;
  label: string;
  available: boolean;
  default_execution_mode?: string | null;
  notes?: string[];
}

interface ExecutionHealth {
  ok: boolean;
  audio?: {
    transports?: AudioTransportCapability[];
    notes?: string[];
  };
}

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

function asRecord(value: unknown): JsonRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : null;
}

function statusColor(status?: string | null) {
  if (status === 'completed' || status === 'pass') return 'var(--success-text)';
  if (status === 'failed' || status === 'fail') return 'var(--error-text)';
  if (status === 'needs_review' || status === 'running' || status === 'queued') return 'var(--warn-text, #9a6700)';
  return 'var(--muted)';
}

function evidenceLine(conversation: ExecutionConversation) {
  const parts: string[] = [];
  const session = conversation.audio_session;
  const provenance = asRecord(session?.runtime_provenance);
  const readiness = asRecord(session?.real_call_readiness);
  if (conversation.mode === 'pipecat_webrtc') parts.push('Pipecat tester → agent proof');
  if (provenance?.fixture_backed_scoring === true || readiness?.scoring === 'fixture_backed') {
    parts.push('sample-based score');
  }
  if (provenance?.evidence_source === 'current_run') parts.push('current-run evidence');
  if (readiness?.browser_webrtc_peer === 'not_connected') parts.push('no live browser peer');
  const url = conversation.recording?.recording_url ?? conversation.recording?.uri;
  if (typeof url === 'string' && url.trim()) parts.push('recording metadata');
  const summary = conversation.vcon_export_summary;
  if (typeof summary?.dialog_turns === 'number') parts.push(`vCon · ${summary.dialog_turns} turns`);
  else if (summary) parts.push('vCon');
  if (typeof session?.frames_sent === 'number' || typeof session?.frames_received === 'number') {
    parts.push(`frames ${session.frames_sent ?? 0}/${session.frames_received ?? 0}`);
  }
  return parts.length ? parts.join(' · ') : null;
}

const localVoiceOption = {
  id: 'pipecat_webrtc',
  label: 'Built-in generalist voice evaluation',
  eyebrow: 'CAE local audio loop',
  description: 'Runs a Pipecat tester against a separate Pipecat agent through rtc-asr, a configured LLM, and Kokoro; evaluation uses only evidence captured in this run.',
  detail: 'Synthetic local media; no browser mic, SIP, or phone call',
  button: 'Run generalist voice evaluation',
} as const;

function transportStatus(health: ExecutionHealth | null, id: string) {
  return health?.audio?.transports?.find((transport) => transport.id === id);
}

function voiceTargets(agents: AgentRecord[]) {
  return agents.filter((agent) => agent.channel === 'voice' && agent.target === 'builtin_sample_voice');
}

function targetBadge(agent?: AgentRecord | null) {
  if (!agent) return 'No target loaded';
  if (agent.target === 'builtin_sample_voice') return 'Built-in generalist agent';
  return agent.target.replaceAll('_', ' ');
}

export function VoiceEvalPage() {
  const [error, setError] = useState<string | null>(null);
  const [isLaunching, setIsLaunching] = useState(false);
  const [run, setRun] = useState<ExecutionRun | null>(null);
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string>('');
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [health, setHealth] = useState<ExecutionHealth | null>(null);

  const active = useMemo(
    () => run?.status === 'queued' || run?.status === 'running',
    [run?.status],
  );
  const targets = useMemo(() => voiceTargets(agents), [agents]);
  const selectedTarget = useMemo(
    () => targets.find((agent) => agent.id === selectedAgentId) ?? targets[0] ?? null,
    [selectedAgentId, targets],
  );
  const selectedMode = localVoiceOption;
  const pipecat = transportStatus(health, 'pipecat_small_webrtc');

  useEffect(() => {
    let cancelled = false;
    fetch(`${getApiBase()}/api/agents`, { cache: 'no-store' })
      .then((response) => handleJson<{ agents?: AgentRecord[] }>(response))
      .then((payload) => {
        if (cancelled) return;
        const nextAgents = payload.agents ?? [];
        setAgents(nextAgents);
        const nextTargets = voiceTargets(nextAgents);
        const preferred = nextTargets.find((agent) => agent.id === 'generalist-voice-agent') ?? nextTargets[0];
        if (preferred) setSelectedAgentId(preferred.id);
      })
      .catch((err) => {
        if (!cancelled) setAgentsError(err instanceof Error ? err.message : 'Could not load targets.');
      });
    fetch(`${getApiBase()}/api/execution/health`, { cache: 'no-store' })
      .then((response) => handleJson<ExecutionHealth>(response))
      .then((payload) => {
        if (!cancelled) setHealth(payload);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

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
            mode: 'pipecat_webrtc',
            iterations: 1,
            user_id: identity.userId,
            project_id: identity.projectId,
            agent_id: selectedTarget?.id,
            model_name: selectedTarget?.metadata?.model_name ?? undefined,
            tester_id: 'pipecat_tester',
            executor_id: 'cae_local_audio_loop',
            evaluate: true,
            audio_transport: 'pipecat_small_webrtc',
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

  const readinessRows = [
    { label: 'Run Agent target', value: selectedTarget ? selectedTarget.name : 'No voice target loaded', state: selectedTarget ? 'ready' : 'warn' },
    { label: 'Execution API', value: health?.ok ? 'Ready' : 'Checking', state: health?.ok ? 'ready' : 'warn' },
    { label: 'Pipecat capture hooks', value: pipecat?.available ? 'Available for proof capture' : 'Unavailable', state: pipecat?.available ? 'ready' : 'warn' },
    { label: 'Browser mic peer', value: 'Not connected in this slice', state: 'blocked' },
    { label: 'SIP/PSTN call', value: 'Deferred until FreeSWITCH/Verto bridge lands', state: 'blocked' },
  ];

  return (
    <section className="voice-eval-workspace" aria-label="Voice evaluation">
      <aside className="voice-scenario-card" aria-labelledby="voice-scenario-title">
        <div className="voice-section-heading">
          <span className="voice-step">Scenario</span>
          <p className="eyebrow">Cancellation rescue</p>
          <h2 id="voice-scenario-title">Can the target retain an at-risk caller?</h2>
          <p>
            A caller asks to cancel. The selected Run Agent target should understand the reason,
            respond appropriately, and leave inspectable evidence without inventing actions.
          </p>
        </div>
        <dl className="voice-scenario-facts">
          <div><dt>Suite</dt><dd>Call center voice AI</dd></div>
          <div><dt>Execution engine</dt><dd>Run Agent</dd></div>
          <div><dt>Current call proof</dt><dd>Sample-based capture</dd></div>
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
        <section className="card voice-mode-panel" aria-labelledby="voice-target-title">
          <div className="voice-panel-heading">
            <div>
              <span className="voice-step">1</span>
              <p className="eyebrow">Target under test</p>
              <h2 id="voice-target-title">Pick the Run Agent target</h2>
            </div>
            <ApiAwareLink className="voice-inline-link" href="/targets">Manage targets</ApiAwareLink>
          </div>

          <div className="voice-target-grid">
            <label className="voice-target-select">
              <span>Voice target</span>
              <select
                value={selectedTarget?.id ?? ''}
                onChange={(event) => setSelectedAgentId(event.target.value)}
                aria-label="Voice target"
              >
                {targets.length ? (
                  targets.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)
                ) : (
                  <option value="">No voice targets available</option>
                )}
              </select>
            </label>
            <div className="voice-target-summary">
              <span>{targetBadge(selectedTarget)}</span>
              <strong>{selectedTarget?.name ?? 'No target loaded'}</strong>
              <p>{selectedTarget?.description ?? 'Create or load a voice target before treating this as a target-backed run.'}</p>
            </div>
          </div>

          {agentsError ? (
            <div className="voice-error" role="alert">
              <strong>Could not load targets</strong>
              <span>{agentsError}</span>
            </div>
          ) : null}
        </section>

        <section className="card voice-mode-panel" aria-labelledby="voice-mode-title">
          <div className="voice-panel-heading">
            <div>
              <span className="voice-step">2</span>
              <p className="eyebrow">Execution path</p>
              <h2 id="voice-mode-title">Choose the level of proof</h2>
            </div>
            <span className="voice-selection-summary">Selected: {selectedMode.label}</span>
          </div>

          <div role="group" aria-label="Voice evaluation mode" className="voice-mode-grid">
            <div className="voice-mode-option" data-selected="true" aria-label={selectedMode.label}>
              <span className="voice-mode-radio" aria-hidden="true">✓</span>
              <span className="voice-mode-copy">
                <small>{selectedMode.eyebrow}</small>
                <strong>{selectedMode.label}</strong>
                <span>{selectedMode.description}</span>
                <em>{selectedMode.detail}</em>
              </span>
            </div>
            <div className="voice-mode-option" data-selected="false">
              <span className="voice-mode-radio" aria-hidden="true">→</span>
              <span className="voice-mode-copy">
                <small>Already have a conversation?</small>
                <strong>Evaluate saved evidence</strong>
                <span>Score an existing transcript or evidence bundle without pretending it is an agent target.</span>
                <ApiAwareLink href="/eval">Open Eval evidence</ApiAwareLink>
              </span>
            </div>
          </div>

          <div className="voice-readiness-panel" aria-label="Voice call readiness">
            <div>
              <p className="eyebrow">Real-call readiness</p>
              <strong>What this run proves today</strong>
            </div>
            <dl>
              {readinessRows.map((row) => (
                <div key={row.label} data-state={row.state}>
                  <dt>{row.label}</dt>
                  <dd>{row.value}</dd>
                </div>
              ))}
            </dl>
          </div>

          {error ? (
            <div className="voice-error" role="alert">
              <strong>Could not start the evaluation</strong>
              <span>{error}</span>
            </div>
          ) : null}

          <div className="voice-launch-bar">
            <div>
              <span className="voice-step">3</span>
              <strong>Run cancellation rescue</strong>
              <small>{selectedTarget ? `${selectedTarget.name} via ${selectedMode.label}` : selectedMode.detail}</small>
            </div>
            <button
              type="button"
              className="voice-run-button"
              aria-label={selectedMode.button}
              onClick={onLaunch}
              disabled={isLaunching || active || !selectedTarget}
            >
              {isLaunching ? 'Starting...' : active ? 'Running...' : selectedMode.button}
              {!isLaunching && !active ? <span aria-hidden="true">→</span> : null}
            </button>
          </div>
        </section>

        <section className="card voice-results-panel" aria-labelledby="voice-results-title" aria-live="polite">
          <div className="voice-panel-heading">
            <div>
              <span className="voice-step">4</span>
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
                <ApiAwareLink className="voice-inline-link" href={`/runs/${encodeURIComponent(run.execution_run_id)}`}>
                  Open Run Agent detail
                </ApiAwareLink>
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
                    <div><strong>Evaluation in progress</strong><p>Waiting for the first conversation evidence...</p></div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="voice-empty-results">
              <div className="voice-empty-icon" aria-hidden="true">◎</div>
              <div>
                <strong>Your evidence will appear here</strong>
                <p>Select a target and proof level to see the score, transcript, captured artifacts, and Run Agent detail link.</p>
              </div>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}
