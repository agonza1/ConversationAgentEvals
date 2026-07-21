'use client';

import { useEffect, useMemo, useState } from 'react';

import { SiteNav } from '@/components/SiteNav';
import { getApiBase } from '@/lib/execution';

interface ListenerEvent {
  sequence: number;
  kind: 'message' | 'audio';
  speaker: string;
  text: string;
  media_url?: string | null;
  direction?: 'tester_to_target' | 'target_to_tester' | null;
  llm_output?: string | null;
  asr_receipt?: string | null;
}

function directionLabel(direction?: ListenerEvent['direction']) {
  if (direction === 'tester_to_target') return 'tester → target';
  if (direction === 'target_to_tester') return 'target → tester';
  return null;
}

interface ListenerConversation {
  conversation_id: string;
  live_events?: ListenerEvent[];
}

interface ListenerState {
  listener: {
    run_status: string;
    read_only: boolean;
    can_inject_audio: boolean;
    requires_microphone: boolean;
  };
  conversations?: ListenerConversation[];
}

function mediaUrl(apiBase: string, value: string) {
  if (/^https?:\/\//i.test(value)) return value;
  return `${apiBase.replace(/\/$/, '')}${value.startsWith('/') ? value : `/${value}`}`;
}

async function fetchJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  const text = await response.text();
  if (!response.ok) throw new Error(text || `Request failed with ${response.status}`);
  return (text ? JSON.parse(text) : {}) as T;
}

export function BrowserListenerPage({ token }: { token: string }) {
  const apiBase = useMemo(() => getApiBase(), []);
  const [state, setState] = useState<ListenerState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function load() {
      try {
        const next = await fetchJson<ListenerState>(mediaUrl(apiBase, `/api/execution/listeners/${encodeURIComponent(token)}`), {
          cache: 'no-store',
        });
        if (!active) return;
        setState(next);
        setError(null);
        if (next.listener.run_status === 'queued' || next.listener.run_status === 'running') {
          timer = setTimeout(() => void load(), 1500);
        }
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : 'Could not load listener.');
        timer = setTimeout(() => void load(), 3000);
      }
    }

    void load();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [apiBase, token]);

  const events = useMemo(
    () => (state?.conversations ?? []).flatMap((conversation) =>
      (conversation.live_events ?? []).map((event) => ({ ...event, conversationId: conversation.conversation_id })),
    ),
    [state],
  );

  return (
    <main className="page-shell">
      <SiteNav current="runs" />
      <section className="minimal-hero" aria-labelledby="listener-title">
        <p className="eyebrow">Run Agent listener</p>
        <h1 id="listener-title">Read-only browser listener</h1>
        <p className="hero-copy">
          {state
            ? `${state.listener.read_only ? 'Read-only' : 'Writable'} · ${state.listener.can_inject_audio ? 'can inject audio' : 'cannot inject audio'} · ${state.listener.requires_microphone ? 'microphone required' : 'no microphone'} · ${state.listener.run_status}`
            : 'Connecting to the live run.'}
        </p>
        {error ? <p role="alert" className="error-text">{error}</p> : null}
      </section>
      <section aria-label="Observed live exchange" style={{ display: 'grid', gap: 10 }}>
        {events.length ? events.map((event) => (
          <article key={`${event.conversationId}-${event.sequence}`} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, display: 'grid', gap: 4 }}>
            <strong>
              {event.speaker}{directionLabel(event.direction) ? ` · ${directionLabel(event.direction)}` : ''}
            </strong>
            <span style={{ whiteSpace: 'pre-wrap' }}>{event.text}</span>
            {event.llm_output && event.asr_receipt ? (
              <span style={{ color: 'var(--muted)', fontSize: 12 }}>
                LLM output: {event.llm_output} · ASR receipt: {event.asr_receipt}
              </span>
            ) : null}
            {event.kind === 'audio' && event.media_url ? (
              <audio controls src={mediaUrl(apiBase, event.media_url)} aria-label={`${event.speaker} audio ${event.sequence}`} />
            ) : null}
          </article>
        )) : (
          <span style={{ color: 'var(--muted)' }}>Waiting for the first observed message from this run...</span>
        )}
      </section>
    </main>
  );
}
