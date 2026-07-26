'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export interface LiveRunEvent {
  sequence: number;
  kind: 'message' | 'audio';
  speaker: string;
  text: string;
  media_url?: string | null;
  mime_type?: string | null;
  direction?: 'tester_to_target' | 'target_to_tester' | null;
  llm_output?: string | null;
  asr_receipt?: string | null;
  frame_metadata?: Record<string, unknown> | null;
  created_at?: string | null;
}

function directionLabel(direction?: LiveRunEvent['direction']) {
  if (direction === 'tester_to_target') return 'tester → target';
  if (direction === 'target_to_tester') return 'target → tester';
  return null;
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
  media_transport?: 'webrtc';
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

type PlaybackMode = 'idle' | 'live' | 'replay';

export function LiveRunFeedback({
  conversations,
  apiBase,
  voice = false,
  executionRunId,
  userId,
  runStatus,
}: LiveRunFeedbackProps) {
  const [expanded, setExpanded] = useState(false);
  const [playbackMode, setPlaybackMode] = useState<PlaybackMode>('idle');
  const [playbackMessage, setPlaybackMessage] = useState<string | null>(null);
  const [listenerToken, setListenerToken] = useState<ListenerToken | null>(null);
  const [listenerConversations, setListenerConversations] = useState<LiveRunConversation[] | null>(null);
  const [listenerMessage, setListenerMessage] = useState<string | null>(null);
  const [isCreatingListener, setIsCreatingListener] = useState(false);
  const playedRef = useRef(new Set<string>());
  const queuedRef = useRef(new Set<string>());
  const playbackModeRef = useRef<PlaybackMode>('idle');
  const playbackGenerationRef = useRef(0);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentResolveRef = useRef<(() => void) | null>(null);
  const playbackRef = useRef(Promise.resolve());
  const listenerActive = runStatus === 'queued' || runStatus === 'running';
  const canCreateListener = voice && Boolean(executionRunId && userId) && listenerActive;
  const displayedConversations = listenerActive && listenerConversations
    ? listenerConversations
    : conversations;
  const events = useMemo(
    () => displayedConversations.flatMap((conversation) =>
      (conversation.live_events ?? []).map((event) => ({ ...event, conversationId: conversation.conversation_id })),
    ),
    [displayedConversations],
  );
  const audioEvents = useMemo(
    () => events.filter((event) => event.kind === 'audio' && event.media_url),
    [events],
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

  const stopPlayback = useCallback((message: string | null = null) => {
    playbackGenerationRef.current += 1;
    playbackModeRef.current = 'idle';
    currentAudioRef.current?.pause();
    currentAudioRef.current = null;
    currentResolveRef.current?.();
    currentResolveRef.current = null;
    queuedRef.current.clear();
    playbackRef.current = Promise.resolve();
    setPlaybackMode('idle');
    setPlaybackMessage(message);
  }, []);

  const queueAudioEvents = useCallback((
    candidates: typeof audioEvents,
    mode: Exclude<PlaybackMode, 'idle'>,
    generation: number,
  ) => {
    for (const event of candidates) {
      const eventKey = `${event.conversationId}:${event.sequence}`;
      const queueKey = `${generation}:${eventKey}`;
      if (
        queuedRef.current.has(queueKey)
        || (mode === 'live' && playedRef.current.has(eventKey))
      ) {
        continue;
      }
      queuedRef.current.add(queueKey);
      playbackRef.current = playbackRef.current.then(async () => {
        if (
          playbackGenerationRef.current !== generation
          || playbackModeRef.current !== mode
        ) {
          queuedRef.current.delete(queueKey);
          return;
        }
        const audio = new Audio(mediaUrl(apiBase, event.media_url as string));
        currentAudioRef.current = audio;
        const finished = new Promise<void>((resolve) => {
          const onFinished = () => resolve();
          currentResolveRef.current = onFinished;
          audio.addEventListener('ended', onFinished, { once: true });
          audio.addEventListener('error', onFinished, { once: true });
        });
        try {
          await audio.play();
          if (mode === 'live') playedRef.current.add(eventKey);
          await finished;
        } catch {
          currentResolveRef.current?.();
          setPlaybackMessage(
            mode === 'live'
              ? 'Live audio was blocked by the browser. Stop and start live listening again.'
              : 'Playback was blocked by the browser. Click play to try again.',
          );
        } finally {
          if (currentAudioRef.current === audio) currentAudioRef.current = null;
          currentResolveRef.current = null;
          queuedRef.current.delete(queueKey);
        }
      }).catch(() => undefined);
    }
    return playbackRef.current;
  }, [apiBase]);

  function startLiveListening() {
    stopPlayback();
    for (const event of audioEvents) {
      playedRef.current.add(`${event.conversationId}:${event.sequence}`);
    }
    playbackModeRef.current = 'live';
    setPlaybackMode('live');
    setExpanded(true);
    setPlaybackMessage('Listening for new audio only. Earlier turns will not replay.');
  }

  function startReplay() {
    stopPlayback();
    if (!audioEvents.length) {
      setPlaybackMessage('No recorded conversation audio is available.');
      return;
    }
    const generation = playbackGenerationRef.current;
    playbackModeRef.current = 'replay';
    setPlaybackMode('replay');
    setExpanded(true);
    setPlaybackMessage('Playing the recorded conversation from the beginning.');
    void queueAudioEvents(audioEvents, 'replay', generation).then(() => {
      if (
        playbackGenerationRef.current === generation
        && playbackModeRef.current === 'replay'
      ) {
        playbackModeRef.current = 'idle';
        setPlaybackMode('idle');
        setPlaybackMessage('Playback finished. Play again to restart from the beginning.');
      }
    });
  }

  useEffect(() => {
    if (playbackMode !== 'live') return;
    const generation = playbackGenerationRef.current;
    const pending = queueAudioEvents(audioEvents, 'live', generation);
    if (!listenerActive) {
      void pending.then(() => {
        if (
          playbackGenerationRef.current === generation
          && playbackModeRef.current === 'live'
        ) {
          playbackModeRef.current = 'idle';
          setPlaybackMode('idle');
          setPlaybackMessage('Live listening ended with the run. Recorded playback is now available.');
        }
      });
    }
  }, [audioEvents, listenerActive, playbackMode, queueAudioEvents]);

  useEffect(() => {
    playedRef.current.clear();
    stopPlayback();
  }, [executionRunId, stopPlayback]);

  useEffect(() => {
    if (!listenerActive) setListenerConversations(null);
  }, [listenerActive]);

  useEffect(() => () => {
    playbackGenerationRef.current += 1;
    currentAudioRef.current?.pause();
    currentResolveRef.current?.();
  }, []);

  useEffect(() => {
    if (!listenerToken) return undefined;
    const token = listenerToken.token;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const status = await refreshListener(token);
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

  const audioButtonLabel = playbackMode === 'live'
    ? 'Stop live listening'
    : playbackMode === 'replay'
      ? 'Stop playback'
      : listenerActive
        ? 'Listen live from now'
        : audioEvents.length
          ? 'Play recorded conversation'
          : 'No recorded audio';
  const defaultPlaybackMessage = listenerActive
    ? 'Live listening starts with the next audio segment and never replays earlier turns.'
    : audioEvents.length
      ? 'Recorded playback starts at the beginning and includes the complete captured conversation.'
      : 'No captured conversation audio is available for playback.';

  return (
    <section aria-label="Live run feedback" style={{ display: 'grid', gap: 8 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <button type="button" onClick={() => setExpanded((value) => !value)}>
          {expanded
            ? (listenerActive ? 'Hide live exchange' : 'Hide conversation evidence')
            : (listenerActive ? 'Show live exchange' : 'Show conversation evidence')}
        </button>
        {voice ? (
          <>
            <button
              type="button"
              aria-pressed={playbackMode !== 'idle'}
              disabled={!listenerActive && !audioEvents.length}
              onClick={() => {
                if (playbackMode !== 'idle') {
                  stopPlayback(
                    listenerActive
                      ? 'Live listening stopped. Start again to hear only future audio.'
                      : 'Playback stopped. Play again to restart from the beginning.',
                  );
                } else if (listenerActive) {
                  startLiveListening();
                } else {
                  startReplay();
                }
              }}
            >
              {audioButtonLabel}
            </button>
            {listenerActive ? (
              <button
                type="button"
                onClick={() => void createListener()}
                disabled={!canCreateListener || isCreatingListener}
              >
                {isCreatingListener ? 'Creating live listener...' : 'Create live listener link'}
              </button>
            ) : null}
          </>
        ) : null}
      </div>
      {voice ? (
        <span role="status" aria-live="polite" style={{ color: 'var(--muted)', fontSize: 13 }}>
          {playbackMessage || defaultPlaybackMessage}
        </span>
      ) : null}
      {voice && (listenerActive || listenerToken) ? (
        <div aria-label="Run listener link" style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12, display: 'grid', gap: 6 }}>
          <strong style={{ fontSize: 13 }}>
            {listenerActive ? 'Read-only live listener' : 'Listener link · run finished'}
          </strong>
          {listenerToken ? (
            <>
              <a href={listenerBrowserUrl(listenerToken.token, apiBase)} target="_blank" rel="noreferrer" style={{ overflowWrap: 'anywhere', fontWeight: 760 }}>
                {listenerBrowserUrl(listenerToken.token, apiBase)}
              </a>
              <span style={{ color: 'var(--muted)', fontSize: 13 }}>
                {listenerToken.read_only ? 'Read-only' : 'Writable'} · {listenerToken.media_transport === 'webrtc' ? 'WebRTC audio' : 'audio'} · {listenerToken.can_inject_audio ? 'can inject audio' : 'cannot inject audio'} · {listenerToken.requires_microphone ? 'microphone required' : 'no microphone'} · expires {new Date(listenerToken.expires_at).toLocaleTimeString()}
              </span>
              <button type="button" onClick={() => void refreshListener()}>
                Refresh listener view
              </button>
            </>
          ) : (
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>
              Available only while this run is active.
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
              <strong style={{ fontSize: 13 }}>
                {event.speaker}{directionLabel(event.direction) ? ` · ${directionLabel(event.direction)}` : ''}
              </strong>
              <span style={{ whiteSpace: 'pre-wrap' }}>{event.text}</span>
              {event.llm_output && event.asr_receipt ? (
                <span style={{ color: 'var(--muted)', fontSize: 12 }}>
                  LLM output: {event.llm_output} · ASR receipt: {event.asr_receipt}
                </span>
              ) : null}
            </div>
          )) : (
            <span style={{ color: 'var(--muted)' }}>
              {listenerActive
                ? 'Waiting for the first observed message from this run…'
                : 'No observed messages were recorded for this completed run.'}
            </span>
          )}
        </div>
      ) : null}
    </section>
  );
}
