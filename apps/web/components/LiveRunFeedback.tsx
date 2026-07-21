'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export interface LiveRunEvent {
  sequence: number;
  kind: 'message' | 'audio';
  speaker: string;
  text: string;
  media_url?: string | null;
  mime_type?: string | null;
  created_at?: string | null;
}

export interface LiveRunConversation {
  conversation_id: string;
  live_events?: LiveRunEvent[];
}

interface LiveRunFeedbackProps {
  conversations: LiveRunConversation[];
  apiBase: string;
  voice?: boolean;
  executionRunId?: string;
  userId?: string;
  runStatus?: string;
}

function mediaUrl(apiBase: string, value: string) {
  if (/^https?:\/\//i.test(value)) return value;
  return `${apiBase.replace(/\/$/, '')}${value.startsWith('/') ? value : `/${value}`}`;
}

function listenerBrowserUrl(token: string, apiBase: string) {
  const params = new URLSearchParams();
  if (apiBase) params.set('api_base', apiBase);
  const query = params.toString();
  return `/listeners/${encodeURIComponent(token)}${query ? `?${query}` : ''}`;
}

async function fetchJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  const text = await response.text();
  if (!response.ok) {
    throw new Error(text || `Request failed with ${response.status}`);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

interface ListenerToken {
  token: string;
  expires_at: string;
  listen_url: string;
  read_only: boolean;
  can_inject_audio: boolean;
  requires_microphone: boolean;
}

interface ListenerState {
  listener: {
    read_only: boolean;
    can_inject_audio: boolean;
    requires_microphone: boolean;
    run_status: string;
  };
  conversations?: LiveRunConversation[];
}

export function LiveRunFeedback({
  conversations,
  apiBase,
  voice = false,
  executionRunId,
  userId,
  runStatus,
}: LiveRunFeedbackProps) {
  const [expanded, setExpanded] = useState(false);
  const [listening, setListening] = useState(false);
  const [listenerToken, setListenerToken] = useState<ListenerToken | null>(null);
  const [listenerConversations, setListenerConversations] = useState<LiveRunConversation[] | null>(null);
  const [listenerMessage, setListenerMessage] = useState<string | null>(null);
  const [isCreatingListener, setIsCreatingListener] = useState(false);
  const playedRef = useRef(new Set<string>());
  const queuedRef = useRef(new Set<string>());
  const listeningRef = useRef(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentResolveRef = useRef<(() => void) | null>(null);
  const playbackRef = useRef(Promise.resolve());
  const listenerActive = runStatus === 'queued' || runStatus === 'running';
  const canCreateListener = voice && Boolean(executionRunId && userId) && listenerActive;
  const displayedConversations = listenerConversations ?? conversations;
  const events = useMemo(
    () => displayedConversations.flatMap((conversation) =>
      (conversation.live_events ?? []).map((event) => ({ ...event, conversationId: conversation.conversation_id })),
    ),
    [displayedConversations],
  );

  const refreshListener = useCallback(async (token = listenerToken?.token) => {
    if (!token) return;
    const next = await fetchJson<ListenerState>(mediaUrl(apiBase, `/api/execution/listeners/${token}`), {
      cache: 'no-store',
    });
    setListenerConversations(next.conversations ?? []);
    setListenerMessage(
      `${next.listener.read_only ? 'Read-only' : 'Writable'} · ${
        next.listener.requires_microphone ? 'microphone required' : 'no microphone'
      } · ${next.listener.run_status}`,
    );
    return next.listener.run_status;
  }, [apiBase, listenerToken?.token]);

  async function createListener() {
    if (!executionRunId || !userId) return;
    setIsCreatingListener(true);
    setListenerMessage(null);
    try {
      const payload = await fetchJson<{ listener: ListenerToken }>(
        mediaUrl(apiBase, `/api/execution/runs/${encodeURIComponent(executionRunId)}/listener-token?user_id=${encodeURIComponent(userId)}`),
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ttl_seconds: 600 }),
        },
      );
      setListenerToken(payload.listener);
      setExpanded(true);
      await refreshListener(payload.listener.token);
    } catch (error) {
      setListenerMessage(error instanceof Error ? error.message : 'Could not create listener.');
    } finally {
      setIsCreatingListener(false);
    }
  }

  useEffect(() => {
    listeningRef.current = listening;
    if (!listening) {
      currentAudioRef.current?.pause();
      currentAudioRef.current = null;
      currentResolveRef.current?.();
      currentResolveRef.current = null;
      return;
    }
    for (const event of events) {
      if (event.kind !== 'audio' || !event.media_url) continue;
      const key = `${event.conversationId}:${event.sequence}:${event.media_url}`;
      if (playedRef.current.has(key) || queuedRef.current.has(key)) continue;
      queuedRef.current.add(key);
      playbackRef.current = playbackRef.current.then(async () => {
        if (!listeningRef.current) {
          queuedRef.current.delete(key);
          return;
        }
        try {
          const audio = new Audio(mediaUrl(apiBase, event.media_url as string));
          currentAudioRef.current = audio;
          await audio.play();
          playedRef.current.add(key);
          await new Promise<void>((resolve) => {
            currentResolveRef.current = resolve;
            audio.addEventListener('ended', () => resolve(), { once: true });
            audio.addEventListener('error', () => resolve(), { once: true });
          });
          currentResolveRef.current = null;
        } finally {
          queuedRef.current.delete(key);
        }
      }).catch(() => undefined);
    }
  }, [apiBase, events, listening]);

  useEffect(() => () => currentAudioRef.current?.pause(), []);

  useEffect(() => {
    if (!listenerToken) return undefined;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const status = await refreshListener(listenerToken.token);
        if (!active) return;
        if (status === undefined || status === 'queued' || status === 'running') {
          timer = setTimeout(() => void poll(), 1500);
        }
      } catch (error) {
        if (!active) return;
        setListenerMessage(error instanceof Error ? error.message : 'Could not refresh listener.');
        timer = setTimeout(() => void poll(), 3000);
      }
    }

    timer = setTimeout(() => void poll(), 1500);
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [listenerToken, refreshListener]);

  return (
    <section aria-label="Live run feedback" style={{ display: 'grid', gap: 8 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <button type="button" onClick={() => setExpanded((value) => !value)}>
          {expanded ? 'Hide live exchange' : 'Show live exchange'}
        </button>
        {voice ? (
          <>
            <button
              type="button"
              aria-pressed={listening}
              onClick={() => {
                setExpanded(true);
                setListening((value) => !value);
              }}
            >
              {listening ? 'Mute live conversation' : 'Unmute live conversation'}
            </button>
            <button
              type="button"
              onClick={() => void createListener()}
              disabled={!canCreateListener || isCreatingListener}
            >
              {isCreatingListener ? 'Creating listener...' : 'Create listener link'}
            </button>
          </>
        ) : null}
      </div>
      {voice ? (
        <div aria-label="Read-only browser listener" style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12, display: 'grid', gap: 6 }}>
          <strong style={{ fontSize: 13 }}>Read-only browser listener</strong>
          {listenerToken ? (
            <>
              <a href={listenerBrowserUrl(listenerToken.token, apiBase)} target="_blank" rel="noreferrer" style={{ overflowWrap: 'anywhere', fontWeight: 760 }}>
                {listenerBrowserUrl(listenerToken.token, apiBase)}
              </a>
              <span style={{ color: 'var(--muted)', fontSize: 13 }}>
                {listenerToken.read_only ? 'Read-only' : 'Writable'} · {listenerToken.can_inject_audio ? 'can inject audio' : 'cannot inject audio'} · {listenerToken.requires_microphone ? 'microphone required' : 'no microphone'} · expires {new Date(listenerToken.expires_at).toLocaleTimeString()}
              </span>
              <button type="button" onClick={() => void refreshListener()}>
                Refresh listener view
              </button>
            </>
          ) : (
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>
              {listenerActive ? 'Available while this run is active.' : 'Available while a voice run is queued or running.'}
            </span>
          )}
          {listenerMessage ? <span role="status" style={{ color: 'var(--muted)', fontSize: 13 }}>{listenerMessage}</span> : null}
        </div>
      ) : null}
      {expanded ? (
        <div
          aria-label="Observed live exchange"
          aria-live="polite"
          style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12, display: 'grid', gap: 8 }}
        >
          {events.length ? events.map((event) => (
            <div key={`${event.conversationId}-${event.sequence}`} style={{ display: 'grid', gap: 2 }}>
              <strong style={{ fontSize: 13 }}>{event.speaker}</strong>
              <span style={{ whiteSpace: 'pre-wrap' }}>{event.text}</span>
            </div>
          )) : (
            <span style={{ color: 'var(--muted)' }}>Waiting for the first observed message from this run…</span>
          )}
        </div>
      ) : null}
    </section>
  );
}
