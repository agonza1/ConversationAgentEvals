'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { askQuestion, bootstrapSession, controlSession, getPipecatAgentState, getPipecatLiveState, getPublicSession, getSessionLiveState, gotoSlide, promptVoicePipelineOpening, sendVoicePipelineQuestion, setAutoplay, startVoicePipeline, stopVoicePipeline } from '@/lib/api';
import { connectRealtimeBrowserSession, RealtimeBrowserSession, RealtimeConnectionStatus, RealtimeToolDefinition } from '@/lib/realtimeClient';
import { connectPipecatSession } from '@/lib/pipecatClient';
import { BootstrapStatus, SessionLiveState, SessionSnapshot, TranscriptEvent, VoicePipelineStatus } from '@/lib/types';
import { SlideStage } from './SlideStage';
import { TranscriptPanel } from './TranscriptPanel';
import { ControlBar } from './ControlBar';
import { QuestionInput } from './QuestionInput';
import { HeyGenAvatarPanel } from './HeyGenAvatarPanel';
import { PresentationSummary } from './PresentationSummary';
import { BootstrapStatusCard } from './BootstrapStatusCard';
import { DemoReadinessCard } from './DemoReadinessCard';
import { LiveOpsCard } from './LiveOpsCard';

interface Props {
  initialData: SessionSnapshot;
}

const SHOW_TESTING_CONTROLS = process.env.NEXT_PUBLIC_PRODUCTION !== 'true';

export function PresentationShell({ initialData }: Props) {
  const [snapshot, setSnapshot] = useState(initialData);
  const [answer, setAnswer] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>('');
  const [lastSyncLabel, setLastSyncLabel] = useState<string>('just now');
  const [bootstrap, setBootstrap] = useState<BootstrapStatus | null>(null);
  const [liveState, setLiveState] = useState<SessionLiveState | null>(null);
  const pollingRef = useRef(false);
  const autoplayAdvanceRef = useRef(false);
  const bootstrapRef = useRef(false);
  const [speaking, setSpeaking] = useState(false);
  const [voicePipeline, setVoicePipeline] = useState<VoicePipelineStatus | null>(null);
  const [liveTranscript, setLiveTranscript] = useState('');
  const [liveAnswer, setLiveAnswer] = useState('');
  const [liveConnectionStatus, setLiveConnectionStatus] = useState<RealtimeConnectionStatus>('idle');
  const [remoteAudioLevel, setRemoteAudioLevel] = useState(0);
  const [avatarVideoActive, setAvatarVideoActive] = useState(false);
  const realtimeClientRef = useRef<RealtimeBrowserSession | null>(null);
  const voiceStartRef = useRef(false);

  const voiceActive = Boolean(
    avatarVideoActive ||
      ['connecting', 'connected', 'responding', 'listening'].includes(liveConnectionStatus) ||
      (voicePipeline && !['idle', 'error', 'disconnected', 'ended'].includes(String(voicePipeline.status))),
  );

  const currentSlide = useMemo(
    () => snapshot.slides.find((slide) => slide.index === snapshot.session.current_slide_index) ?? snapshot.slides[0],
    [snapshot],
  );

  const activeVoiceLine = useMemo(() => {
    const transcript = [...snapshot.transcript]
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
      .filter((event): event is TranscriptEvent => event.role === 'agent' && Boolean(event.text?.trim()));
    return transcript.at(-1)?.text?.trim() ?? currentSlide?.talk_track ?? '';
  }, [currentSlide?.talk_track, snapshot.transcript]);

  const sessionId = snapshot.session.id;
  const sessionToken = snapshot.session.public_token;

  const refresh = useCallback(async (options?: { preferStatus?: SessionSnapshot['session']['status']; retries?: number }) => {
    if (pollingRef.current) return;
    pollingRef.current = true;
    try {
      const retries = options?.retries ?? 1;
      for (let attempt = 0; attempt < retries; attempt += 1) {
        const next = await getPublicSession(sessionToken);
        const preferStatus = options?.preferStatus;
        if (!preferStatus || next.session.status === preferStatus || attempt === retries - 1) {
          setSnapshot(next);
          try {
            const nextLive = await getSessionLiveState(next.session.id);
            setLiveState(nextLive);
          } catch {
            // Keep snapshot-driven UI usable even if live state fetch fails.
          }
          setLastSyncLabel(new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }));
          return next;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 300));
      }
    } finally {
      pollingRef.current = false;
    }
    return undefined;
  }, [sessionToken]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      void refresh().catch((err) => {
        setError((current) => current || (err instanceof Error ? err.message : 'Refresh failed'));
      });
    }, 2500);

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        void refresh().catch(() => undefined);
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [refresh]);

  useEffect(() => {
    if (bootstrapRef.current) return;
    bootstrapRef.current = true;
    void Promise.all([
      bootstrapSession(sessionId),
      getSessionLiveState(sessionId).catch(() => null),
    ])
      .then(([bootstrapData, liveData]) => {
        setBootstrap(bootstrapData);
        if (liveData) setLiveState(liveData);
      })
      .catch((err) => {
        setError((current) => current || (err instanceof Error ? err.message : 'Bootstrap failed'));
      })
      .finally(() => {
        bootstrapRef.current = false;
      });
  }, [sessionId]);

  useEffect(() => {
    if (!snapshot.session.autoplay_enabled || snapshot.session.status !== 'presenting') {
      autoplayAdvanceRef.current = false;
      return;
    }

    const startedAt = snapshot.session.autoplay_started_at
      ? new Date(snapshot.session.autoplay_started_at).getTime()
      : Date.now();
    const elapsed = Math.max(0, Date.now() - startedAt);
    const delay = Math.max(250, snapshot.session.autoplay_interval_seconds * 1000 - elapsed);

    const timeout = window.setTimeout(() => {
      if (autoplayAdvanceRef.current || snapshot.session.current_slide_index >= snapshot.slides.length - 1) {
        return;
      }
      autoplayAdvanceRef.current = true;
      void controlSession(sessionId, 'advance-autoplay')
        .then(() => refresh())
        .catch((err) => {
          setError(err instanceof Error ? err.message : 'Autoplay advance failed');
        })
        .finally(() => {
          autoplayAdvanceRef.current = false;
        });
    }, delay);

    return () => window.clearTimeout(timeout);
  }, [refresh, sessionId, snapshot.session.autoplay_enabled, snapshot.session.autoplay_interval_seconds, snapshot.session.autoplay_started_at, snapshot.session.current_slide_index, snapshot.session.status, snapshot.slides.length]);

  useEffect(() => {
    setSpeaking(
      snapshot.session.status === 'presenting' ||
        snapshot.session.status === 'answering' ||
        voicePipeline?.status === 'speaking' ||
        liveConnectionStatus === 'responding',
    );
  }, [snapshot.session.current_slide_index, snapshot.session.status, currentSlide?.talk_track, voicePipeline?.status, liveConnectionStatus]);

  useEffect(() => {
    return () => {
      void realtimeClientRef.current?.disconnect();
    };
  }, []);

  const apiOrigin = useMemo(() => {
    const configured = process.env.NEXT_PUBLIC_API_BASE_URL;
    if (configured) {
      return configured.replace(/\/$/, '');
    }
    if (typeof window !== 'undefined') {
      return window.location.origin;
    }
    return '';
  }, []);

  const fetchRealtimeJson = useCallback(async (path: string, init?: RequestInit) => {
    const response = await fetch(`${apiOrigin}${path}`, {
      cache: 'no-store',
      ...init,
    });
    if (!response.ok) {
      throw new Error(`Tool request failed with ${response.status}`);
    }
    return response.json();
  }, [apiOrigin]);

  const realtimeTools = useMemo<RealtimeToolDefinition[]>(() => {
    const manifest = bootstrap?.realtime?.tool_manifest ?? snapshot.realtime?.tool_manifest;
    if (manifest?.length) {
      return manifest.map((tool) => ({
        name: tool.name,
        description: tool.description,
        parameters: tool.parameters,
      }));
    }

    return [
      {
        name: 'get_current_slide',
        description: 'Get the current presentation slide and its grounded content.',
        parameters: {
          type: 'object',
          properties: {},
          additionalProperties: false,
        },
      },
      {
        name: 'search_slides',
        description: 'Search deck slides for relevant content by query.',
        parameters: {
          type: 'object',
          properties: {
            query: { type: 'string', description: 'Search query for slide content.' },
          },
          required: ['query'],
          additionalProperties: false,
        },
      },
      {
        name: 'get_slide_content',
        description: 'Fetch the full content for a specific slide index.',
        parameters: {
          type: 'object',
          properties: {
            slide_index: { type: 'number', description: '0-based slide index.' },
          },
          required: ['slide_index'],
          additionalProperties: false,
        },
      },
      {
        name: 'next_slide',
        description: 'Advance the presentation to the next slide.',
        parameters: {
          type: 'object',
          properties: {},
          additionalProperties: false,
        },
      },
      {
        name: 'prev_slide',
        description: 'Go back to the previous slide.',
        parameters: {
          type: 'object',
          properties: {},
          additionalProperties: false,
        },
      },
      {
        name: 'goto_slide',
        description: 'Jump to a specific 0-based slide index.',
        parameters: {
          type: 'object',
          properties: {
            slide_index: { type: 'number', description: '0-based slide index.' },
          },
          required: ['slide_index'],
          additionalProperties: false,
        },
      },
      {
        name: 'restart_current_slide',
        description: 'Repeat the current slide from the top.',
        parameters: {
          type: 'object',
          properties: {},
          additionalProperties: false,
        },
      },
      {
        name: 'pause_presentation',
        description: 'Pause the presentation.',
        parameters: {
          type: 'object',
          properties: {},
          additionalProperties: false,
        },
      },
      {
        name: 'resume_presentation',
        description: 'Resume the presentation.',
        parameters: {
          type: 'object',
          properties: {},
          additionalProperties: false,
        },
      },
    ];
  }, [bootstrap?.realtime?.tool_manifest, snapshot.realtime?.tool_manifest]);

  const handleRealtimeTool = useCallback(
    async (name: string, args: Record<string, unknown>) => {
      const apiBase = apiOrigin;

      if (name === 'get_current_slide') {
        return fetchRealtimeJson(`/api/sessions/${sessionId}/current-slide`);
      }
      if (name === 'search_slides') {
        const query = String(args.query ?? '').trim();
        return fetchRealtimeJson(`/api/sessions/${sessionId}/search-slides?query=${encodeURIComponent(query)}`);
      }
      if (name === 'get_slide_content') {
        const slideIndex = Number(args.slide_index ?? snapshot.session.current_slide_index);
        return fetchRealtimeJson(`/api/realtime/sessions/${sessionId}/slide-content/${slideIndex}`);
      }
      if (name === 'next_slide') {
        const response = await fetch(`${apiBase}/api/sessions/${sessionId}/next-slide`, { method: 'POST' });
        if (!response.ok) throw new Error(`Tool request failed with ${response.status}`);
        const result = await response.json();
        await refresh({ retries: 3 });
        return result;
      }
      if (name === 'prev_slide') {
        const response = await fetch(`${apiBase}/api/sessions/${sessionId}/prev-slide`, { method: 'POST' });
        if (!response.ok) throw new Error(`Tool request failed with ${response.status}`);
        const result = await response.json();
        await refresh({ retries: 3 });
        return result;
      }
      if (name === 'goto_slide') {
        const slideIndex = Number(args.slide_index ?? snapshot.session.current_slide_index);
        const response = await fetch(`${apiBase}/api/sessions/${sessionId}/goto-slide`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ index: slideIndex }),
        });
        if (!response.ok) throw new Error(`Tool request failed with ${response.status}`);
        const result = await response.json();
        await refresh({ retries: 3 });
        return result;
      }
      if (name === 'restart_current_slide') {
        const response = await fetch(`${apiBase}/api/sessions/${sessionId}/restart-current-slide`, { method: 'POST' });
        if (!response.ok) throw new Error(`Tool request failed with ${response.status}`);
        const result = await response.json();
        await refresh({ retries: 3 });
        return result;
      }
      if (name === 'pause_presentation') {
        const response = await fetch(`${apiBase}/api/sessions/${sessionId}/pause`, { method: 'POST' });
        if (!response.ok) throw new Error(`Tool request failed with ${response.status}`);
        const result = await response.json();
        await refresh({ retries: 3 });
        return result;
      }
      if (name === 'resume_presentation') {
        const response = await fetch(`${apiBase}/api/sessions/${sessionId}/resume`, { method: 'POST' });
        if (!response.ok) throw new Error(`Tool request failed with ${response.status}`);
        const result = await response.json();
        await refresh({ retries: 3 });
        return result;
      }
      throw new Error(`Unsupported realtime tool: ${name}`);
    },
    [apiOrigin, fetchRealtimeJson, refresh, sessionId, snapshot.session.current_slide_index],
  );

  async function handleStartVoice() {
    if (voiceStartRef.current) return;
    voiceStartRef.current = true;
    setError('');
    setLiveTranscript('');
    setLiveAnswer('');
    setRemoteAudioLevel(0);
    setAvatarVideoActive(false);
    setVoicePipeline({ status: 'listening', mode: 'starting' });

    try {
      await realtimeClientRef.current?.disconnect();
      realtimeClientRef.current = null;
    } catch {
      // Best-effort cleanup before opening a fresh live transport.
    }

    let pipeline: VoicePipelineStatus;
    try {
      pipeline = await startVoicePipeline(sessionId);
      setVoicePipeline(pipeline);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Voice pipeline failed to start.';
      setError(message);
      setLiveConnectionStatus('error');
      setVoicePipeline((current) => (current ? { ...current, status: 'error' } : current));
      voiceStartRef.current = false;
      return;
    }

    const clientSecret = pipeline.transport?.client_secret;
    const model = (pipeline.transport?.live_session as Record<string, unknown> | undefined)?.model as string | undefined;
    const instructions = pipeline.agent?.instructions ?? pipeline.transport?.instructions ?? pipeline.instructions ?? bootstrap?.agent?.instructions ?? bootstrap?.realtime?.instructions ?? null;
    const pipecatBaseUrl = '/pipecat';
    const pipecatConnectUrl = `${pipecatBaseUrl}/sessions/${sessionId}/live/create`;
    const pipecatStopUrl = `${pipecatBaseUrl}/sessions/${sessionId}/disconnect`;

    if (pipeline.mode === 'pipecat-orchestrated' && pipecatConnectUrl) {
      try {
        const handle = await connectPipecatSession({
          sessionId,
          connectUrl: pipecatConnectUrl,
          stopUrl: pipecatStopUrl,
          onStatusChange: (status) => {
            setLiveConnectionStatus(status as RealtimeConnectionStatus);
            setVoicePipeline((current) => (current ? { ...current, status } : current));
          },
          onRemoteAudioLevel: setRemoteAudioLevel,
          onRemoteVideo: () => setAvatarVideoActive(true),
          onError: (message) => setError(message),
        });
        realtimeClientRef.current = handle as unknown as RealtimeBrowserSession;
        const [latestAgent, latestLive] = await Promise.all([
          getPipecatAgentState(sessionId).catch(() => null),
          getPipecatLiveState(sessionId).catch(() => null),
        ]);
        setVoicePipeline((current) =>
          current
            ? {
                ...current,
                status: latestAgent?.agent_status ?? latestLive?.live?.state ?? 'listening',
                agent: latestAgent ?? current.agent ?? null,
                transport: current.transport
                  ? {
                      ...current.transport,
                      live_session: latestLive?.live ?? current.transport.live_session ?? null,
                    }
                  : current.transport,
              }
            : current,
        );
        voiceStartRef.current = false;
        return;
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Pipecat transport connection failed.';
        setError(message);
        setLiveConnectionStatus('error');
        setVoicePipeline((current) => (current ? { ...current, status: 'error' } : current));
        voiceStartRef.current = false;
        return;
      }
    }

    if (clientSecret) {
      try {
        realtimeClientRef.current = await connectRealtimeBrowserSession({
          sessionId,
          ephemeralKey: clientSecret,
          model: model ?? bootstrap?.realtime?.model ?? snapshot.realtime?.model,
          instructions,
          tools: realtimeTools,
          toolHandler: handleRealtimeTool,
          onStatusChange: (status) => {
            setLiveConnectionStatus(status);
            setVoicePipeline((current) =>
              current
                ? {
                    ...current,
                    status:
                      status === 'connected' || status === 'listening'
                        ? 'listening'
                        : status === 'responding'
                          ? 'speaking'
                          : status === 'disconnected'
                            ? 'idle'
                            : status,
                  }
                : current,
            );
          },
          onError: (message) => setError(message),
          onTranscript: (transcript) => {
            setLiveTranscript(transcript);
            setVoicePipeline((current) => (current ? { ...current, transcript } : current));
            void fetchRealtimeJson(`/api/realtime/sessions/${sessionId}/transcript`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ role: 'user', text: transcript }),
            }).catch(() => undefined);
          },
          onOutputTranscript: (transcript, final) => {
            const normalized = transcript.trim();
            setLiveAnswer(normalized);
            setAnswer(normalized);
            if (final) {
              setVoicePipeline((current) => (current ? { ...current, answer: normalized, status: 'listening' } : current));
              const latestQuestion = liveTranscript || transcript;
              void fetchRealtimeJson(`/api/realtime/sessions/${sessionId}/answer`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question: latestQuestion, answer: normalized }),
              })
                .then(() => refresh({ retries: 2 }))
                .catch(() => undefined);
            }
          },
        });
        setVoicePipeline((current) =>
          current
            ? {
                ...current,
                mode: 'openai-realtime-browser',
              }
            : current,
        );
        voiceStartRef.current = false;
        return;
      } catch (error) {
        setError(error instanceof Error ? error.message : 'Realtime transport connection failed.');
        setLiveConnectionStatus('error');
      }
    }

    setVoicePipeline((current) => (current ? { ...current, status: 'error' } : { status: 'error', mode: 'provider-error' }));
    setError('Realtime voice transport is not configured. Set OPENAI_API_KEY and run the Pipecat service, or use simulated voice/text Q&A for a non-live proof.');
    voiceStartRef.current = false;
  }

  async function handleStopVoice() {
    voiceStartRef.current = false;
    await realtimeClientRef.current?.disconnect();
    realtimeClientRef.current = null;
    setLiveConnectionStatus('disconnected');
    setAvatarVideoActive(false);
    setRemoteAudioLevel(0);
    const result = await stopVoicePipeline(sessionId);
    setVoicePipeline({
      ...result,
      status: 'disconnected',
    });
    setLiveTranscript('');
    setLiveAnswer('');
  }

  async function promptOpeningMonologue() {
    try {
      const result = await promptVoicePipelineOpening(sessionId);
      const agentStatus = (result as VoicePipelineStatus & { agent_status?: string }).agent_status;
      setVoicePipeline((current) => ({
        ...(current ?? { mode: 'pipecat-orchestrated' }),
        ...result,
        status: agentStatus ?? result.status ?? 'speaking',
      }));
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Could not queue opening presenter audio.');
    }
  }

  async function handleStartPresentation() {
    await controlSession(sessionId, 'start');
    const started = await getPublicSession(sessionToken);
    if (!realtimeClientRef.current?.isConnected()) {
      await handleStartVoice();
    }
    await promptOpeningMonologue();
    return started;
  }

  async function run(
    action: () => Promise<SessionSnapshot | unknown>,
    options?: { preferStatus?: SessionSnapshot['session']['status']; refreshRetries?: number },
  ) {
    setBusy(true);
    setError('');
    try {
      const result = await action();
      const hasSnapshotShape = result && typeof result === 'object' && 'session' in (result as Record<string, unknown>) && 'deck' in (result as Record<string, unknown>);
      const hasSessionOnlyShape = result && typeof result === 'object'
        && 'status' in (result as Record<string, unknown>)
        && ('current_slide_index' in (result as Record<string, unknown>) || 'public_token' in (result as Record<string, unknown>) || 'id' in (result as Record<string, unknown>));
      const nextStatus = hasSnapshotShape
        ? ((result as SessionSnapshot).session.status as SessionSnapshot['session']['status'] | undefined)
        : hasSessionOnlyShape
          ? ((result as { status?: SessionSnapshot['session']['status'] }).status)
          : undefined;
      const optimisticSnapshot = hasSnapshotShape
        ? (result as SessionSnapshot)
        : hasSessionOnlyShape
          ? {
              ...snapshot,
              session: result as SessionSnapshot['session'],
            }
          : null;
      if (optimisticSnapshot) {
        setSnapshot(optimisticSnapshot);
        console.log('[presentation-shell] optimistic session status', optimisticSnapshot.session.status);
      }
      const refreshed = await refresh({
        preferStatus: optimisticSnapshot ? undefined : (options?.preferStatus ?? nextStatus),
        retries: options?.refreshRetries ?? 3,
      });
      if (!refreshed && optimisticSnapshot) {
        setSnapshot((current) => ({
          ...optimisticSnapshot,
          transcript: current.transcript.length > optimisticSnapshot.transcript.length ? current.transcript : optimisticSnapshot.transcript,
          avatar: null,
          realtime: current.realtime ?? optimisticSnapshot.realtime,
        }));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 24 }}>
      <div className="card" style={{ padding: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20, gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
          <div>
            <p style={{ margin: 0, color: 'var(--muted)', fontSize: 14 }}>Deck</p>
            <h1 style={{ margin: '6px 0 0', fontSize: 30 }}>{snapshot.deck.title}</h1>
          </div>
          <div style={{ textAlign: 'right' }}>
            <p style={{ margin: 0, color: 'var(--muted)', fontSize: 14 }}>State</p>
            <strong style={{ textTransform: 'capitalize', display: 'block' }}>{snapshot.session.status}</strong>
            <span style={{ color: 'var(--muted)', fontSize: 12 }}>Live sync: {lastSyncLabel}</span>
          </div>
        </div>

        <SlideStage slide={currentSlide} />

        <ControlBar
          status={snapshot.session.status}
          currentSlideIndex={snapshot.session.current_slide_index}
          slideCount={snapshot.slides.length}
          busy={busy}
          autoplayEnabled={snapshot.session.autoplay_enabled}
          autoplayIntervalSeconds={snapshot.session.autoplay_interval_seconds}
          onStart={() => run(handleStartPresentation, { preferStatus: 'presenting', refreshRetries: 8 })}
          onPause={() => run(async () => ({
            session: await controlSession(sessionId, 'pause'),
            deck: snapshot.deck,
            slides: snapshot.slides,
            transcript: snapshot.transcript,
            avatar: null,
            realtime: snapshot.realtime,
          }))}
          onResume={() => run(async () => ({
            session: await controlSession(sessionId, 'resume'),
            deck: snapshot.deck,
            slides: snapshot.slides,
            transcript: snapshot.transcript,
            avatar: null,
            realtime: snapshot.realtime,
          }))}
          onEnd={() => run(async () => ({
            session: await controlSession(sessionId, 'end'),
            deck: snapshot.deck,
            slides: snapshot.slides,
            transcript: snapshot.transcript,
            avatar: null,
            realtime: snapshot.realtime,
          }))}
          onPrev={() => run(async () => {
            await controlSession(sessionId, 'prev-slide');
            return getPublicSession(sessionToken);
          }, { refreshRetries: 6 })}
          onNext={() => run(async () => {
            await controlSession(sessionId, 'next-slide');
            return getPublicSession(sessionToken);
          }, { refreshRetries: 6 })}
          onGoto={(index) => run(async () => {
            await gotoSlide(sessionId, index);
            return getPublicSession(sessionToken);
          }, { refreshRetries: 6 })}
          onAutoplayToggle={(enabled) => run(async () => {
            await setAutoplay(sessionId, enabled, snapshot.session.autoplay_interval_seconds);
            return getPublicSession(sessionToken);
          }, { refreshRetries: 6 })}
          onAutoplayIntervalChange={(seconds) => run(async () => {
            await setAutoplay(sessionId, snapshot.session.autoplay_enabled, seconds);
            return getPublicSession(sessionToken);
          }, { refreshRetries: 6 })}
        />

        <QuestionInput
          busy={busy}
          voiceActive={voiceActive}
          voiceStatus={voiceActive ? 'Live voice connected' : liveConnectionStatus === 'disconnected' ? 'Voice disconnected' : 'Voice idle'}
          showTestingControls={SHOW_TESTING_CONTROLS}
          onSubmit={(question) =>
            run(async () => {
              const result = await askQuestion(sessionId, question);
              setAnswer(result.answer);
            })
          }
          onSimulateVoice={(question) =>
            run(async () => {
              const result = await askQuestion(sessionId, `[Simulated voice] ${question}`);
              setAnswer(result.answer);
            })
          }
          onStartVoice={() => run(handleStartVoice)}
          onStopVoice={() => run(handleStopVoice)}
        />

        {voicePipeline ? (
          <div className="card" style={{ marginTop: 16, padding: 16, background: '#ecfeff' }}>
            <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>Voice pipeline</p>
            <p style={{ margin: '6px 0 0', lineHeight: 1.6 }}>
              Status: <strong>{voicePipeline.status}</strong>
              {voicePipeline.mode ? ` · ${voicePipeline.mode}` : ''}
              {liveConnectionStatus !== 'idle' ? ` · transport ${liveConnectionStatus}` : ''}
            </p>
            {(voicePipeline.transcript || liveTranscript) ? <p style={{ margin: '6px 0 0', lineHeight: 1.6 }}>Last transcript: {voicePipeline.transcript ?? liveTranscript}</p> : null}
            {liveAnswer ? <p style={{ margin: '6px 0 0', lineHeight: 1.6 }}>Live answer: {liveAnswer}</p> : null}
            {voicePipeline.transport ? (
              <div style={{ marginTop: 10, display: 'grid', gap: 6 }}>
                <p style={{ margin: 0, color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase' }}>Transport</p>
                <p style={{ margin: 0, lineHeight: 1.6 }}>
                  Provider: <strong>{String((voicePipeline.transport as Record<string, unknown>).provider ?? 'unknown')}</strong>
                  {typeof (voicePipeline.transport as Record<string, unknown>).configured === 'boolean'
                    ? ` · ${((voicePipeline.transport as Record<string, unknown>).configured as boolean) ? 'configured' : 'not configured'}`
                    : ''}
                </p>
                {(voicePipeline.transport as Record<string, unknown>).client_secret ? (
                  <p style={{ margin: 0, lineHeight: 1.6, color: '#047857' }}>
                    Ephemeral provider token returned and browser transport is ready to connect directly to OpenAI Realtime.
                  </p>
                ) : String((voicePipeline.transport as Record<string, unknown>).provider ?? '') === 'pipecat' ? (
                  <p style={{ margin: 0, lineHeight: 1.6, color: '#047857' }}>
                    Pipecat transport is connected; realtime credentials stay server-side in the orchestrator.
                  </p>
                ) : (
                  <p style={{ margin: 0, lineHeight: 1.6, color: 'var(--muted)' }}>
                    No live provider token yet — configure Realtime transport before using live voice.
                  </p>
                )}
                {(() => {
                  const live = (voicePipeline.transport as Record<string, unknown>).live_session as Record<string, unknown> | undefined;
                  const events = Array.isArray(live?.events) ? live.events as Array<Record<string, unknown>> : [];
                  const helpfulEvents = events.filter((event) => {
                    const type = String(event.type ?? '');
                    const payload = event.payload as Record<string, unknown> | undefined;
                    return type.includes('failed') || type.includes('error') || Boolean(payload?.error) || Boolean(payload?.reason);
                  }).slice(-3);
                  if (!live?.last_error && helpfulEvents.length === 0 && error === '') return null;
                  return (
                    <div style={{ marginTop: 8, padding: 10, borderRadius: 10, background: '#fff7ed', color: '#9a3412', fontSize: 12, lineHeight: 1.5 }}>
                      <strong>Runtime detail</strong>
                      {error ? <p style={{ margin: '4px 0 0' }}>{error}</p> : null}
                      {live?.last_error ? <p style={{ margin: '4px 0 0' }}>{String(live.last_error)}</p> : null}
                      {helpfulEvents.map((event, index) => {
                        const payload = event.payload as Record<string, unknown> | undefined;
                        return <p key={`${String(event.type)}-${index}`} style={{ margin: '4px 0 0' }}>{String(event.type)}: {String(payload?.error ?? payload?.reason ?? '')}</p>;
                      })}
                    </div>
                  );
                })()}
              </div>
            ) : null}
          </div>
        ) : null}

        {answer ? (
          <div className="card" style={{ marginTop: 16, padding: 16, background: 'var(--panel-alt)' }}>
            <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>Latest answer</p>
            <p style={{ marginBottom: 0, lineHeight: 1.6 }}>{answer}</p>
          </div>
        ) : null}

        {error ? <p style={{ color: 'var(--error-text)' }}>{error}</p> : null}
      </div>

      <div style={{ display: 'grid', gap: 24 }}>
        <HeyGenAvatarPanel
          talkTrack={activeVoiceLine}
          speaking={speaking}
          voiceActive={voiceActive}
        />
        <DemoReadinessCard snapshot={snapshot} bootstrap={bootstrap} voice={voicePipeline} />
        <LiveOpsCard live={liveState} />
        <BootstrapStatusCard bootstrap={bootstrap} />
        <PresentationSummary slide={currentSlide} transcript={snapshot.transcript} session={snapshot.session} />
        <TranscriptPanel transcript={snapshot.transcript} />
      </div>
    </div>
  );
}
