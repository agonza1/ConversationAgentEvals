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

function listenerTokenExpired(listener: ListenerToken, now = Date.now()) {
  const expiresAt = Date.parse(listener.expires_at);
  return !Number.isFinite(expiresAt) || expiresAt <= now;
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
  webrtc_url?: string;
  webrtc_ice_url?: string;
  webrtc_stop_url?: string;
  ice_servers?: RTCIceServer[];
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
  const [webrtcStatus, setWebrtcStatus] = useState<'idle' | 'connecting' | 'listening' | 'fallback' | 'error'>('idle');
  const queuedRef = useRef(new Set<string>());
  const playbackModeRef = useRef<PlaybackMode>('idle');
  const playbackGenerationRef = useRef(0);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentResolveRef = useRef<(() => void) | null>(null);
  const playbackRef = useRef(Promise.resolve());
  const liveSegmentFallbackRef = useRef(false);
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const liveAudioRef = useRef<HTMLAudioElement | null>(null);
  const stopUrlRef = useRef<string | null>(null);
  const executionRunIdRef = useRef(executionRunId);
  const listenerTokenRef = useRef(listenerToken);
  executionRunIdRef.current = executionRunId;
  listenerTokenRef.current = listenerToken;
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
    const requestedRunId = executionRunIdRef.current;
    const next = await fetchJson<ListenerState>(mediaUrl(apiBase, `/api/execution/listeners/${token}`), {
      cache: 'no-store',
    });
    if (
      executionRunIdRef.current !== requestedRunId
      || listenerTokenRef.current?.token !== token
    ) {
      return;
    }
    setListenerConversations(next.conversations ?? []);
    setListenerMessage(
      `${next.listener.read_only ? 'Read-only' : 'Writable'} · ${
        next.listener.requires_microphone ? 'microphone required' : 'no microphone'
      } · ${next.listener.run_status}`,
    );
    return next.listener.run_status;
  }, [apiBase, listenerToken?.token]);

  async function requestListenerToken(): Promise<ListenerToken | null> {
    if (!executionRunId || !userId) return null;
    const requestedRunId = executionRunId;
    const payload = await fetchJson<{ listener: ListenerToken }>(
      mediaUrl(apiBase, `/api/execution/runs/${encodeURIComponent(requestedRunId)}/listener-token?user_id=${encodeURIComponent(userId)}`),
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ttl_seconds: 600 }),
      },
    );
    if (executionRunIdRef.current !== requestedRunId) return null;
    return payload.listener;
  }

  async function createListener(): Promise<ListenerToken | null> {
    setIsCreatingListener(true);
    setListenerMessage(null);
    try {
      const token = await requestListenerToken();
      if (!token) return null;
      listenerTokenRef.current = token;
      setListenerToken(token);
      setExpanded(true);
      await refreshListener(token.token);
      return token;
    } catch (error) {
      setListenerMessage(error instanceof Error ? error.message : 'Could not create listener.');
      return null;
    } finally {
      setIsCreatingListener(false);
    }
  }

  const disconnectWebRTC = useCallback((notifyServer = true) => {
    const peer = peerRef.current;
    peerRef.current = null;
    peer?.close();
    if (liveAudioRef.current) liveAudioRef.current.srcObject = null;
    const stopUrl = stopUrlRef.current;
    stopUrlRef.current = null;
    if (notifyServer && stopUrl) {
      void fetch(mediaUrl(apiBase, stopUrl), { method: 'POST', keepalive: true }).catch(() => undefined);
    }
    setWebrtcStatus('idle');
  }, [apiBase]);

  const connectWebRTC = useCallback(async (
    token: ListenerToken,
    isCurrentAttempt: () => boolean,
  ) => {
    if (!token.webrtc_url) {
      throw new Error('This listener token does not expose WebRTC signaling.');
    }
    disconnectWebRTC();
    const peer = new RTCPeerConnection({ iceServers: token.ice_servers ?? [] });
    const stopUrl = token.webrtc_stop_url
      ? mediaUrl(apiBase, token.webrtc_stop_url)
      : null;
    peerRef.current = peer;
    stopUrlRef.current = stopUrl;
    const pendingIceCandidates: RTCIceCandidateInit[] = [];
    let signalingReady = false;
    let serverListenerAttached = false;
    const ownsSharedConnection = () => peerRef.current === peer;
    const cleanupAttempt = async (notifyServer: boolean) => {
      const ownedSharedConnection = ownsSharedConnection();
      peer.close();
      if (ownedSharedConnection) {
        peerRef.current = null;
        if (liveAudioRef.current) liveAudioRef.current.srcObject = null;
        if (stopUrlRef.current === stopUrl) stopUrlRef.current = null;
        setWebrtcStatus('idle');
      }
      if (notifyServer && stopUrl) {
        await fetch(stopUrl, { method: 'POST' }).catch(() => undefined);
      }
      return ownedSharedConnection;
    };
    setWebrtcStatus('connecting');
    peer.addTransceiver('audio', { direction: 'recvonly' });
    peer.ontrack = (event) => {
      if (
        !ownsSharedConnection()
        || !isCurrentAttempt()
        || event.track.kind !== 'audio'
        || !liveAudioRef.current
      ) return;
      liveAudioRef.current.srcObject = event.streams[0] ?? new MediaStream([event.track]);
      void liveAudioRef.current.play().catch(() => {
        setPlaybackMessage('Live audio was blocked by the browser. Stop listening and try again.');
      });
    };
    peer.onconnectionstatechange = () => {
      if (!ownsSharedConnection() || !isCurrentAttempt()) return;
      if (peer.connectionState === 'connected') {
        liveSegmentFallbackRef.current = false;
        setWebrtcStatus('listening');
        setPlaybackMessage('Listening to the ongoing WebRTC audio stream. Earlier audio is not replayed.');
      } else if (peer.connectionState === 'failed') {
        setWebrtcStatus('error');
        setPlaybackMessage('The live WebRTC listener failed. Stop listening and try again while the run is active.');
      }
    };
    const sendIceCandidate = async (candidate: RTCIceCandidateInit) => {
      if (!token.webrtc_ice_url) return;
      await fetchJson(mediaUrl(apiBase, token.webrtc_ice_url), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ candidate }),
      });
    };
    peer.onicecandidate = (event) => {
      if (
        !ownsSharedConnection()
        || !isCurrentAttempt()
        || !event.candidate
        || !token.webrtc_ice_url
      ) return;
      const candidate = event.candidate.toJSON();
      if (!signalingReady) {
        pendingIceCandidates.push(candidate);
        return;
      }
      void sendIceCandidate(candidate).catch(() => {
        if (!ownsSharedConnection() || !isCurrentAttempt()) return;
        setWebrtcStatus('error');
        setPlaybackMessage('Could not send a WebRTC ICE candidate.');
      });
    };
    try {
      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      const answer = await fetchJson<{ answer: RTCSessionDescriptionInit; status?: string }>(
        mediaUrl(apiBase, token.webrtc_url),
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sdp: offer.sdp, type: offer.type }),
        },
      );
      serverListenerAttached = true;
      if (!ownsSharedConnection() || !isCurrentAttempt()) {
        await cleanupAttempt(true);
        return;
      }
      await peer.setRemoteDescription(answer.answer);
      signalingReady = true;
      for (const candidate of pendingIceCandidates) await sendIceCandidate(candidate);
      if (!ownsSharedConnection() || !isCurrentAttempt()) {
        await cleanupAttempt(true);
        return;
      }
      // The signaling response only confirms that the server accepted the
      // offer. ICE may still be unable to establish the media connection.
      setWebrtcStatus(peer.connectionState === 'connected' ? 'listening' : 'connecting');
    } catch (error) {
      const ownedSharedConnection = await cleanupAttempt(serverListenerAttached);
      if (ownedSharedConnection && isCurrentAttempt()) setWebrtcStatus('error');
      throw error;
    }
  }, [apiBase, disconnectWebRTC]);

  const stopPlayback = useCallback((message: string | null = null) => {
    playbackGenerationRef.current += 1;
    playbackModeRef.current = 'idle';
    currentAudioRef.current?.pause();
    currentAudioRef.current = null;
    currentResolveRef.current?.();
    currentResolveRef.current = null;
    queuedRef.current.clear();
    liveSegmentFallbackRef.current = false;
    playbackRef.current = Promise.resolve();
    disconnectWebRTC();
    setPlaybackMode('idle');
    setPlaybackMessage(message);
  }, [disconnectWebRTC]);

  const queueAudioEvents = useCallback((
    candidates: typeof audioEvents,
    generation: number,
    expectedMode: Exclude<PlaybackMode, 'idle'> = 'replay',
  ) => {
    for (const event of candidates) {
      const eventKey = `${event.conversationId}:${event.sequence}`;
      const queueKey = `${generation}:${eventKey}`;
      if (queuedRef.current.has(queueKey)) continue;
      queuedRef.current.add(queueKey);
      playbackRef.current = playbackRef.current.then(async () => {
        if (
          playbackGenerationRef.current !== generation
          || playbackModeRef.current !== expectedMode
        ) {
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
          await finished;
        } catch {
          currentResolveRef.current?.();
          setPlaybackMessage('Playback was blocked by the browser. Click play to try again.');
        } finally {
          if (currentAudioRef.current === audio) currentAudioRef.current = null;
          currentResolveRef.current = null;
        }
      }).catch(() => undefined);
    }
    return playbackRef.current;
  }, [apiBase]);

  async function startLiveListening() {
    stopPlayback();
    const generation = playbackGenerationRef.current;
    playbackModeRef.current = 'live';
    setPlaybackMode('live');
    setExpanded(true);
    // Mark audio that existed before the click as heard. If WebRTC cannot
    // connect, the fallback starts with the next captured turn.
    for (const event of audioEvents) {
      queuedRef.current.add(`${generation}:${event.conversationId}:${event.sequence}`);
    }
    setPlaybackMessage('Connecting to the ongoing WebRTC audio stream…');
    let token: ListenerToken | null = null;
    try {
      // The displayed token may already be attached in a shared browser.
      // Give the owner's embedded listener a distinct server-side listener ID.
      token = await requestListenerToken();
    } catch (error) {
      if (
        playbackGenerationRef.current !== generation
        || playbackModeRef.current !== 'live'
      ) {
        return;
      }
      playbackModeRef.current = 'idle';
      setPlaybackMode('idle');
      setPlaybackMessage(error instanceof Error ? error.message : 'Could not create the owner listener.');
      return;
    }
    if (
      playbackGenerationRef.current !== generation
      || playbackModeRef.current !== 'live'
    ) {
      return;
    }
    if (!token) {
      playbackModeRef.current = 'idle';
      setPlaybackMode('idle');
      return;
    }
    listenerTokenRef.current = token;
    setListenerToken(token);
    await refreshListener(token.token).catch(() => undefined);
    try {
      const isCurrentAttempt = () => (
        playbackGenerationRef.current === generation
        && playbackModeRef.current === 'live'
      );
      await connectWebRTC(token, isCurrentAttempt);
      window.setTimeout(() => {
        if (!isCurrentAttempt() || peerRef.current?.connectionState === 'connected') return;
        disconnectWebRTC();
        liveSegmentFallbackRef.current = true;
        setWebrtcStatus('fallback');
        setPlaybackMessage('WebRTC could not reach the local voice runtime. Playing each new audio turn over the live HTTP fallback.');
        void queueAudioEvents(audioEvents, generation, 'live');
      }, 2500);
    } catch (error) {
      if (
        playbackGenerationRef.current !== generation
        || playbackModeRef.current !== 'live'
      ) {
        return;
      }
      playbackModeRef.current = 'idle';
      setPlaybackMode('idle');
      setPlaybackMessage(error instanceof Error ? error.message : 'Could not attach the live WebRTC listener.');
    }
  }

  useEffect(() => {
    if (playbackMode !== 'live' || !liveSegmentFallbackRef.current) return;
    void queueAudioEvents(audioEvents, playbackGenerationRef.current, 'live');
  }, [audioEvents, playbackMode, queueAudioEvents]);

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
    void queueAudioEvents(audioEvents, generation).then(() => {
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
    if (!listenerActive) {
      disconnectWebRTC();
      if (liveSegmentFallbackRef.current) {
        const generation = playbackGenerationRef.current;
        void playbackRef.current.then(() => {
          if (
            playbackGenerationRef.current !== generation
            || playbackModeRef.current !== 'live'
          ) return;
          liveSegmentFallbackRef.current = false;
          playbackModeRef.current = 'idle';
          setPlaybackMode('idle');
          setPlaybackMessage('Live listening ended with the run. Recorded playback is now available.');
        });
        return;
      }
      playbackModeRef.current = 'idle';
      setPlaybackMode('idle');
      setPlaybackMessage('Live listening ended with the run. Recorded playback is now available.');
    }
  }, [disconnectWebRTC, listenerActive, playbackMode]);

  useEffect(() => {
    stopPlayback();
    listenerTokenRef.current = null;
    setListenerToken(null);
    setListenerConversations(null);
    setListenerMessage(null);
  }, [executionRunId, stopPlayback]);

  useEffect(() => {
    if (!listenerActive) setListenerConversations(null);
  }, [listenerActive]);

  useEffect(() => () => {
    playbackGenerationRef.current += 1;
    currentAudioRef.current?.pause();
    currentResolveRef.current?.();
    disconnectWebRTC();
  }, [disconnectWebRTC]);

  useEffect(() => {
    if (!listenerToken) return undefined;
    const listener = listenerToken;
    const token = listener.token;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      if (listenerTokenExpired(listener)) {
        if (!active) return;
        listenerTokenRef.current = null;
        setListenerToken(null);
        setListenerConversations(null);
        setListenerMessage('Live listener expired. Start listening to create a fresh listener.');
        return;
      }
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
    ? 'Stop live WebRTC'
    : playbackMode === 'replay'
      ? 'Stop playback'
      : listenerActive
        ? 'Listen to live WebRTC'
        : audioEvents.length
          ? 'Play recorded conversation'
          : 'No recorded audio';
  const defaultPlaybackMessage = listenerActive
    ? 'Hear audio as each agent produces it. Starting live listening never replays earlier turns.'
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
                  void startLiveListening();
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
        <>
          <span
            role="status"
            aria-live="polite"
            style={{
              color: webrtcStatus === 'fallback' ? 'var(--error-text)' : 'var(--muted)',
              fontSize: 13,
              fontWeight: webrtcStatus === 'fallback' ? 700 : undefined,
            }}
          >
            {playbackMessage || defaultPlaybackMessage}
          </span>
          {listenerActive ? (
            <span
              aria-label="WebRTC listener status"
              data-transport-status={webrtcStatus}
              style={{
                color: webrtcStatus === 'fallback' ? 'var(--error-text)' : 'var(--muted)',
                background: webrtcStatus === 'fallback' ? 'var(--error-bg)' : undefined,
                border: webrtcStatus === 'fallback' ? '1px solid var(--error-border)' : undefined,
                borderRadius: webrtcStatus === 'fallback' ? 8 : undefined,
                padding: webrtcStatus === 'fallback' ? '5px 8px' : undefined,
                fontSize: 12,
                fontWeight: webrtcStatus === 'fallback' ? 800 : undefined,
                width: 'fit-content',
              }}
            >
              {webrtcStatus === 'fallback' ? 'Live audio · HTTP fallback' : `WebRTC · ${webrtcStatus}`}
            </span>
          ) : null}
          <audio ref={liveAudioRef} autoPlay aria-label="Receive-only live run audio" />
        </>
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
              <span style={{ whiteSpace: 'pre-wrap' }}>
                {event.llm_output ? 'Spoken (LLM output): ' : ''}{event.llm_output || event.text}
              </span>
              {event.asr_receipt ? (
                <span style={{ color: 'var(--muted)', fontSize: 12 }}>
                  Peer ASR receipt: {event.asr_receipt}
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
