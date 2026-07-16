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
  const url = conversation.recording?.recording_url ?? conversation.recording?.uri;
  if (typeof url === 'string' && url.trim()) parts.push('recording');
  const summary = conversation.vcon_export_summary;
  if (typeof summary?.dialog_turns === 'number') parts.push(`vCon · ${summary.dialog_turns} turns`);
  else if (summary) parts.push('vCon');
  return parts.length ? parts.join(' · ') : null;
}

export function VoiceEvalPage() {
  const [mode, setMode] = useState<VoiceMode>('voice_fixture');
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

  return (
    <section className="card" style={{ padding: 24, display: 'grid', gap: 16 }} aria-label="Voice evaluation">
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'end' }}>
        <label style={{ display: 'grid', gap: 6, minWidth: 220, flex: 1 }}>
          <span style={{ fontWeight: 700 }}>Mode</span>
          <select
            aria-label="Voice evaluation mode"
            value={mode}
            onChange={(event) => setMode(event.target.value as VoiceMode)}
            style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}
          >
            <option value="voice_fixture">Fixture</option>
            <option value="pipecat_webrtc">Pipecat mock</option>
          </select>
        </label>
        <button
          type="button"
          onClick={onLaunch}
          disabled={isLaunching || active}
          style={{
            border: 'none',
            borderRadius: 10,
            padding: '12px 18px',
            background: 'var(--accent)',
            color: 'white',
            fontWeight: 700,
            cursor: isLaunching || active ? 'not-allowed' : 'pointer',
            opacity: isLaunching || active ? 0.65 : 1,
          }}
        >
          {isLaunching ? 'Starting…' : active ? 'Running…' : 'Run'}
        </button>
      </div>

      <p style={{ margin: 0, color: 'var(--muted)', fontSize: 14 }}>
        {mode === 'pipecat_webrtc' ? 'Local mock hooks + vCon. Not live WebRTC.' : 'Offline audio fixture. No live call.'}
      </p>

      {error ? <p style={{ margin: 0, color: 'var(--error-text)' }}>{error}</p> : null}

      {run ? (
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <span>
              <strong style={{ color: statusColor(run.status), textTransform: 'capitalize' }}>{run.status}</strong>
              <span style={{ marginLeft: 8, color: 'var(--muted)', fontSize: 13 }}>{run.execution_run_id}</span>
            </span>
            <span style={{ color: 'var(--muted)', fontSize: 14 }}>
              {run.progress.completed_conversations}/{run.progress.total_conversations}
            </span>
          </div>

          <div aria-label="Voice eval conversations" style={{ display: 'grid', gap: 8 }}>
            {(run.conversations || []).length ? (
              [...run.conversations].reverse().map((conversation) => {
                const evidence = evidenceLine(conversation);
                return (
                  <article
                    key={conversation.conversation_id}
                    style={{
                      border: '1px solid var(--border)',
                      borderRadius: 10,
                      padding: '12px 14px',
                      display: 'grid',
                      gap: 6,
                      background: 'white',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                      <strong>{conversation.scenario_title || conversation.scenario_id}</strong>
                      <span style={{ color: statusColor(conversation.verdict || conversation.status), textTransform: 'capitalize' }}>
                        {conversation.verdict || conversation.status}
                        {typeof conversation.score === 'number' ? ` · ${conversation.score}` : ''}
                      </span>
                    </div>
                    {evidence ? <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)' }}>{evidence}</p> : null}
                    {conversation.error ? (
                      <p style={{ margin: 0, fontSize: 13, color: 'var(--error-text)' }}>{conversation.error}</p>
                    ) : null}
                    {conversation.transcript ? (
                      <p style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 13, maxHeight: 96, overflow: 'hidden' }}>
                        {conversation.transcript}
                      </p>
                    ) : null}
                  </article>
                );
              })
            ) : (
              <p style={{ margin: 0, color: 'var(--muted)' }}>Waiting…</p>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
