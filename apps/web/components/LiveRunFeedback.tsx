'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

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
}

function mediaUrl(apiBase: string, value: string) {
  if (/^https?:\/\//i.test(value)) return value;
  return `${apiBase.replace(/\/$/, '')}${value.startsWith('/') ? value : `/${value}`}`;
}

export function LiveRunFeedback({ conversations, apiBase, voice = false }: LiveRunFeedbackProps) {
  const [expanded, setExpanded] = useState(false);
  const [listening, setListening] = useState(false);
  const playedRef = useRef(new Set<string>());
  const queuedRef = useRef(new Set<string>());
  const listeningRef = useRef(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentResolveRef = useRef<(() => void) | null>(null);
  const playbackRef = useRef(Promise.resolve());
  const events = useMemo(
    () => conversations.flatMap((conversation) =>
      (conversation.live_events ?? []).map((event) => ({ ...event, conversationId: conversation.conversation_id })),
    ),
    [conversations],
  );

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
      const key = `${event.conversationId}:${event.sequence}`;
      if (playedRef.current.has(key) || queuedRef.current.has(key)) continue;
      queuedRef.current.add(key);
      playbackRef.current = playbackRef.current.then(async () => {
        if (!listeningRef.current) {
          queuedRef.current.delete(key);
          return;
        }
        playedRef.current.add(key);
        queuedRef.current.delete(key);
        const audio = new Audio(mediaUrl(apiBase, event.media_url as string));
        currentAudioRef.current = audio;
        await audio.play();
        await new Promise<void>((resolve) => {
          currentResolveRef.current = resolve;
          audio.addEventListener('ended', () => resolve(), { once: true });
          audio.addEventListener('error', () => resolve(), { once: true });
        });
        currentResolveRef.current = null;
      }).catch(() => undefined);
    }
  }, [apiBase, events, listening]);

  useEffect(() => () => currentAudioRef.current?.pause(), []);

  return (
    <section aria-label="Live run feedback" style={{ display: 'grid', gap: 8 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <button type="button" onClick={() => setExpanded((value) => !value)}>
          {expanded ? 'Hide live exchange' : 'Show live exchange'}
        </button>
        {voice ? (
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
        ) : null}
      </div>
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
