'use client';

import Link from 'next/link';
import type { Route } from 'next';
import { useEffect, useMemo, useState } from 'react';

type VoiceMode = 'voice_fixture' | 'pipecat_webrtc';

type JsonRecord = Record<string, unknown>;

interface ExecutionConversation {
  conversation_id: string;
  scenario_id: string;
  scenario_title?: string | null;
  mode: VoiceMode | string;
  status: string;
  iteration?: number;
  turns?: Array<{ speaker?: string | null; text?: string | null }>;
  transcript?: string | null;
  recording?: JsonRecord | null;
  vcon_export?: JsonRecord | null;
  vcon_export_summary?: JsonRecord | null;
  audio_session?: JsonRecord | null;
  verdict?: string | null;
  score?: number | null;
  error?: string | null;
}

interface ExecutionRun {
  execution_run_id: string;
  status: string;
  mode: string;
  progress: {
    completed_conversations: number;
    total_conversations: number;
    percent: number;
  };
  conversations: ExecutionConversation[];
  inference_set_path?: string | null;
  error?: string | null;
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

function recordingSummary(recording?: JsonRecord | null) {
  if (!recording) return null;
  const url = recording.recording_url ?? recording.uri;
  if (typeof url !== 'string' || !url.trim()) return null;
  const mime = typeof recording.mime_type === 'string' ? ` (${recording.mime_type})` : '';
  return `${url}${mime}`;
}

function vconSummary(conversation: ExecutionConversation) {
  const summary = conversation.vcon_export_summary;
  const exportPayload = conversation.vcon_export;
  const source =
    (typeof summary?.source_format === 'string' && summary.source_format) ||
    (typeof exportPayload?.source_format === 'string' && exportPayload.source_format) ||
    null;
  const dialogTurns =
    typeof summary?.dialog_turns === 'number'
      ? summary.dialog_turns
      : Array.isArray(exportPayload?.dialog)
        ? exportPayload.dialog.length
        : null;
  const recordingAttached =
    typeof summary?.recording_attached === 'boolean'
      ? summary.recording_attached
      : Boolean(recordingSummary(conversation.recording));
  if (dialogTurns == null && !source) return null;
  return [
    source ? `source ${source}` : null,
    dialogTurns != null ? `${dialogTurns} dialog turns` : null,
    recordingAttached ? 'recording attached' : null,
  ]
    .filter(Boolean)
    .join(' · ');
}

export function VoiceEvalPage() {
  const [mode, setMode] = useState<VoiceMode>('voice_fixture');
  const [iterations, setIterations] = useState(1);
  const [message, setMessage] = useState<string | null>(null);
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
    setMessage(null);
    try {
      const queued = await handleJson<ExecutionRun>(
        await fetch(`${getApiBase()}/api/execution/runs`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            suite_id: 'call-center-voice-ai',
            scenario_ids: ['cancellation-rescue'],
            mode,
            iterations,
            user_id: identity.userId,
            project_id: identity.projectId,
            evaluate: true,
            audio_transport: mode === 'pipecat_webrtc' ? 'pipecat_small_webrtc' : 'none',
          }),
        }),
      );
      setRun(queued);
      setMessage(
        mode === 'pipecat_webrtc'
          ? 'Queued in-process Pipecat WebRTC mock hooks with vCon capture (no browser peer / FreeSWITCH).'
          : 'Queued voice fixture execution on call-center-voice-ai / cancellation-rescue.',
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not launch voice eval.');
    } finally {
      setIsLaunching(false);
    }
  }

  return (
    <section className="card" style={{ padding: 24, display: 'grid', gap: 16 }} aria-label="Voice evaluation">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div>
          <p className="eyebrow" style={{ margin: '0 0 6px' }}>
            Voice eval
          </p>
          <h2 style={{ margin: 0, fontSize: 26 }}>Run a voice scenario evaluation.</h2>
          <p style={{ margin: '8px 0 0', color: 'var(--muted)', maxWidth: 720 }}>
            First-class voice path for Execute-stage audio evidence. Uses{' '}
            <strong>call-center-voice-ai / cancellation-rescue</strong>. Live browser WebRTC and FreeSWITCH Verto SIP
            remain follow-on; this page ships fixture + in-process mock hooks with vCon capture today.
          </p>
        </div>
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
          {isLaunching ? 'Launching…' : active ? 'Voice eval running…' : 'Launch voice eval'}
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
        <label style={{ display: 'grid', gap: 8 }}>
          <span style={{ fontWeight: 700 }}>Voice mode</span>
          <select
            aria-label="Voice evaluation mode"
            value={mode}
            onChange={(event) => setMode(event.target.value as VoiceMode)}
            style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}
          >
            <option value="voice_fixture">Voice fixture (ACC audio plan)</option>
            <option value="pipecat_webrtc">Pipecat hooks (in-process mock)</option>
          </select>
        </label>
        <label style={{ display: 'grid', gap: 8 }}>
          <span style={{ fontWeight: 700 }}>Iterations</span>
          <input
            aria-label="Voice eval iterations"
            type="number"
            min={1}
            max={20}
            value={iterations}
            onChange={(event) => setIterations(Math.max(1, Math.min(20, Number(event.target.value) || 1)))}
            style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}
          />
        </label>
      </div>

      <p style={{ margin: 0, color: 'var(--muted)', fontSize: 14 }}>
        {mode === 'pipecat_webrtc'
          ? 'In-process mock of Pipecat small WebRTC send/receive — synthetic recording/transcription into vCon. Verdict stays fixture-backed. No browser peer or FreeSWITCH.'
          : 'Offline ACC audio fixture scheduler path. No live SIP/WebRTC. Good baseline before mock WebRTC hooks.'}
      </p>

      <p style={{ margin: 0, fontSize: 14 }}>
        Also available inside the{' '}
        <Link href="/benchmarks">benchmark runner</Link> Launch evaluation panel. Live presenter WebRTC demos stay on{' '}
        <Link href={'/present' as Route}>/present</Link>.
      </p>

      {message ? <p style={{ margin: 0, color: 'var(--muted)' }}>{message}</p> : null}
      {error ? <p style={{ margin: 0, color: 'var(--error-text)' }}>{error}</p> : null}

      {run ? (
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', borderTop: '1px solid var(--border)', paddingTop: 12 }}>
            <div>
              <strong>{run.execution_run_id}</strong>
              <span style={{ marginLeft: 10, color: statusColor(run.status), fontWeight: 800, textTransform: 'capitalize' }}>
                {run.status}
              </span>
            </div>
            <div style={{ color: 'var(--muted)', fontSize: 14 }}>
              {run.progress.completed_conversations}/{run.progress.total_conversations} conversations · {run.progress.percent}%
              {run.inference_set_path ? ` · ${run.inference_set_path}` : ''}
            </div>
          </div>

          <div aria-label="Voice eval conversations" style={{ display: 'grid', gap: 8 }}>
            {(run.conversations || []).length ? (
              [...run.conversations].reverse().map((conversation) => {
                const recording = recordingSummary(conversation.recording);
                const vcon = vconSummary(conversation);
                const session = conversation.audio_session;
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
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                      <div>
                        <strong>{conversation.scenario_title || conversation.scenario_id}</strong>
                        <span style={{ marginLeft: 8, color: 'var(--muted)', fontSize: 13 }}>
                          {conversation.mode} · iter {conversation.iteration ?? 1}
                        </span>
                      </div>
                      <span style={{ color: statusColor(conversation.status), fontWeight: 800, textTransform: 'capitalize' }}>
                        {conversation.status}
                      </span>
                    </div>
                    <div style={{ color: 'var(--muted)', fontSize: 13, display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                      <span>{conversation.turns?.length ?? 0} turns</span>
                      {conversation.verdict ? (
                        <span style={{ color: statusColor(conversation.verdict), textTransform: 'capitalize' }}>
                          {conversation.verdict}
                          {typeof conversation.score === 'number' ? ` · ${conversation.score}` : ''}
                          {conversation.mode === 'pipecat_webrtc' ? ' (fixture-backed)' : ''}
                        </span>
                      ) : null}
                      {conversation.error ? <span style={{ color: 'var(--error-text)' }}>{conversation.error}</span> : null}
                    </div>
                    {recording ? (
                      <p style={{ margin: 0, fontSize: 13 }}>
                        <strong>Recording:</strong> {recording}
                      </p>
                    ) : null}
                    {vcon ? (
                      <p style={{ margin: 0, fontSize: 13 }}>
                        <strong>vCon:</strong> {vcon}
                      </p>
                    ) : null}
                    {session ? (
                      <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)' }}>
                        <strong>Audio session:</strong>{' '}
                        {[
                          typeof session.tester_status === 'string' ? `tester ${session.tester_status}` : null,
                          typeof session.frames_sent === 'number' ? `sent ${session.frames_sent}` : null,
                          typeof session.frames_received === 'number' ? `recv ${session.frames_received}` : null,
                        ]
                          .filter(Boolean)
                          .join(' · ') || 'present'}
                      </p>
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
              <p style={{ margin: 0, color: 'var(--muted)' }}>Waiting for the first voice conversation…</p>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
