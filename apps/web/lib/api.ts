import {
  AskResponse,
  BenchmarkRunPayload,
  BenchmarkRunResponse,
  BenchmarkSimulationResponse,
  BenchmarkSuite,
  BootstrapStatus,
  DeckSlidesResponse,
  DeckSummary,
  DefaultDeckMeta,
  EvalRunResponse,
  PipecatAgentStatus,
  PipecatLiveCreateResponse,
  SessionCreateResponse,
  SessionLiveState,
  SessionSnapshot,
  VoicePipelineStatus,
} from './types';

function normalizeApiBase(value: string) {
  return value.replace(/\/$/, '').replace(/\/api$/, '');
}

function getBrowserOrigin() {
  if (typeof window === 'undefined') return null;
  return window.location.origin;
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
      // Ignore malformed override and fall back to env config.
    }
  }

  return '';
}

function getPipecatBase() {
  if (typeof window === 'undefined') {
    return (process.env.PIPECAT_SERVICE_URL ?? process.env.NEXT_PUBLIC_PIPECAT_SERVICE_URL ?? 'http://localhost:8110').replace(/\/$/, '');
  }

  const fromQuery = new URLSearchParams(window.location.search).get('pipecat_base');
  if (fromQuery) {
    try {
      return new URL(fromQuery, window.location.origin).toString().replace(/\/$/, '');
    } catch {
      // Ignore malformed override and fall back to the same-origin proxy.
    }
  }

  // Browser clients should use the same-origin Next rewrite. Public localhost
  // env values break when the UI is opened through a forwarded/tunneled URL,
  // because localhost then points at the browser device instead of this stack.
  return `${getBrowserOrigin() ?? ''}/pipecat`.replace(/\/$/, '');
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    let message = text || `Request failed with ${response.status}`;

    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (parsed?.detail) {
        message = parsed.detail;
      }
    } catch {
      // Keep plain-text fallback.
    }

    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export async function getPublicSession(token: string): Promise<SessionSnapshot> {
  const response = await fetch(`${getApiBase()}/api/public/${token}`, { cache: 'no-store' });
  return handleResponse<SessionSnapshot>(response);
}

export function getPublicSessionUrl(token: string): string {
  const browserOrigin = getBrowserOrigin();
  if (browserOrigin) {
    return `${browserOrigin}/present/${token}`;
  }

  const configuredWebBase = process.env.NEXT_PUBLIC_WEB_BASE_URL ?? process.env.WEB_BASE_URL;
  if (configuredWebBase) {
    return `${configuredWebBase.replace(/\/$/, '')}/present/${token}`;
  }

  return `${getApiBase()}/present/${token}`;
}

export async function controlSession(sessionId: string, action: 'start' | 'pause' | 'resume' | 'end' | 'next-slide' | 'prev-slide' | 'advance-autoplay', body?: Record<string, unknown>) {
  const response = await fetch(`${getApiBase()}/api/sessions/${sessionId}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });

  return handleResponse(response);
}

export async function gotoSlide(sessionId: string, index: number) {
  const response = await fetch(`${getApiBase()}/api/sessions/${sessionId}/goto-slide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index }),
  });

  return handleResponse(response);
}

export async function setAutoplay(sessionId: string, enabled: boolean, intervalSeconds?: number) {
  const response = await fetch(`${getApiBase()}/api/sessions/${sessionId}/autoplay`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled, interval_seconds: intervalSeconds }),
  });

  return handleResponse(response);
}

export async function askQuestion(sessionId: string, question: string): Promise<AskResponse> {
  const response = await fetch(`${getApiBase()}/api/sessions/${sessionId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });

  return handleResponse<AskResponse>(response);
}

export async function uploadDeck(file: File): Promise<DeckSummary> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${getApiBase()}/api/decks`, {
    method: 'POST',
    body: formData,
  });

  return handleResponse<DeckSummary>(response);
}

export async function getDefaultDeckMeta(): Promise<DefaultDeckMeta> {
  const response = await fetch(`${getApiBase()}/api/decks/default-meta`, { cache: 'no-store' });
  return handleResponse<DefaultDeckMeta>(response);
}

export async function createDefaultDeck(): Promise<DeckSummary> {
  const response = await fetch(`${getApiBase()}/api/decks/use-default`, {
    method: 'POST',
  });

  return handleResponse<DeckSummary>(response);
}

export async function getDeck(deckId: string): Promise<DeckSummary> {
  const response = await fetch(`${getApiBase()}/api/decks/${deckId}`, { cache: 'no-store' });
  return handleResponse<DeckSummary>(response);
}

export async function getDeckSlides(deckId: string): Promise<DeckSlidesResponse> {
  const response = await fetch(`${getApiBase()}/api/decks/${deckId}/slides`, { cache: 'no-store' });
  return handleResponse<DeckSlidesResponse>(response);
}

export async function createSession(deckId: string): Promise<SessionCreateResponse> {
  const response = await fetch(`${getApiBase()}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ deck_id: deckId }),
  });

  return handleResponse<SessionCreateResponse>(response);
}

export async function runVoiceEval(payload: { title?: string; conversation: string; criteria: string }): Promise<EvalRunResponse> {
  let conversation: string | Record<string, unknown> | unknown[] = payload.conversation;
  try {
    conversation = JSON.parse(payload.conversation) as Record<string, unknown> | unknown[];
  } catch {
    conversation = payload.conversation;
  }

  const response = await fetch(`${getApiBase()}/api/evals/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title: payload.title,
      conversation,
      criteria: payload.criteria,
    }),
  });

  return handleResponse<EvalRunResponse>(response);
}

export async function listBenchmarkSuites(): Promise<BenchmarkSuite[]> {
  const response = await fetch(`${getApiBase()}/api/benchmarks/suites`, { cache: 'no-store' });
  return handleResponse<BenchmarkSuite[]>(response);
}

export async function getBenchmarkSuite(id: string): Promise<BenchmarkSuite> {
  const response = await fetch(`${getApiBase()}/api/benchmarks/suites/${id}`, { cache: 'no-store' });
  return handleResponse<BenchmarkSuite>(response);
}

export async function runBenchmarkScenario(payload: BenchmarkRunPayload): Promise<BenchmarkRunResponse> {
  const response = await fetch(`${getApiBase()}/api/benchmarks/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  return handleResponse<BenchmarkRunResponse>(response);
}

export async function simulateBenchmarkScenario(payload: { suite_id: string; scenario_id: string; agent_profile?: string; include_failure?: boolean }): Promise<BenchmarkSimulationResponse> {
  const response = await fetch(`${getApiBase()}/api/benchmarks/simulate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  return handleResponse<BenchmarkSimulationResponse>(response);
}


export async function getRealtimeContract(sessionId: string) {
  const response = await fetch(`${getApiBase()}/api/realtime/sessions/${sessionId}/contract`, { cache: 'no-store' });
  return handleResponse(response);
}

export async function bootstrapSession(sessionId: string): Promise<BootstrapStatus> {
  const response = await fetch(`${getApiBase()}/api/bootstrap/sessions/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });

  return handleResponse<BootstrapStatus>(response);
}

export async function getSessionLiveState(sessionId: string): Promise<SessionLiveState> {
  const response = await fetch(`${getApiBase()}/api/sessions/${sessionId}/live`, { cache: 'no-store' });
  return handleResponse<SessionLiveState>(response);
}

export async function createPipecatLiveSession(sessionId: string): Promise<PipecatLiveCreateResponse> {
  const response = await fetch(`${getPipecatBase()}/sessions/${sessionId}/live/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });

  return handleResponse<PipecatLiveCreateResponse>(response);
}

export async function getPipecatLiveState(sessionId: string): Promise<PipecatLiveCreateResponse> {
  const response = await fetch(`${getPipecatBase()}/sessions/${sessionId}/live/state`, {
    cache: 'no-store',
  });

  return handleResponse<PipecatLiveCreateResponse>(response);
}

export async function stopPipecatLiveSession(sessionId: string): Promise<PipecatLiveCreateResponse> {
  const response = await fetch(`${getPipecatBase()}/sessions/${sessionId}/live/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });

  return handleResponse<PipecatLiveCreateResponse>(response);
}

export async function startPipecatAgent(sessionId: string): Promise<PipecatAgentStatus> {
  const response = await fetch(`${getPipecatBase()}/sessions/${sessionId}/agent/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });

  return handleResponse<PipecatAgentStatus>(response);
}

export async function getPipecatAgentState(sessionId: string): Promise<PipecatAgentStatus> {
  const response = await fetch(`${getPipecatBase()}/sessions/${sessionId}/agent/state`, {
    cache: 'no-store',
  });

  return handleResponse<PipecatAgentStatus>(response);
}

export async function stopPipecatAgent(sessionId: string): Promise<PipecatAgentStatus> {
  const response = await fetch(`${getPipecatBase()}/sessions/${sessionId}/agent/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });

  return handleResponse<PipecatAgentStatus>(response);
}

export async function startVoicePipeline(sessionId: string): Promise<VoicePipelineStatus> {
  const bootstrapResponse = await fetch(`${getApiBase()}/api/bootstrap/sessions/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  const bootstrap = await handleResponse<BootstrapStatus>(bootstrapResponse);

  const agent = await startPipecatAgent(sessionId);

  return {
    status: agent.agent_status ?? agent.status,
    mode: 'pipecat-orchestrated',
    instructions: agent.instructions ?? bootstrap.nextStep ?? null,
    agent,
    transport: {
      provider: 'pipecat',
      configured: true,
      connect_url: `/sessions/${sessionId}/live/create`,
      model: (agent.realtime as Record<string, unknown> | undefined)?.model as string | undefined,
      live_session: agent.live_session ?? null,
      instructions: agent.instructions ?? null,
    },
  };
}

export async function sendVoicePipelineQuestion(sessionId: string, transcript: string): Promise<VoicePipelineStatus> {
  const response = await fetch(`${getPipecatBase()}/sessions/${sessionId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript }),
  });

  return handleResponse<VoicePipelineStatus>(response);
}

export async function promptVoicePipelineOpening(sessionId: string): Promise<VoicePipelineStatus> {
  const response = await fetch(`${getPipecatBase()}/sessions/${sessionId}/present-current`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });

  return handleResponse<VoicePipelineStatus>(response);
}

export async function stopVoicePipeline(sessionId: string): Promise<VoicePipelineStatus> {
  const live = await stopPipecatLiveSession(sessionId).catch(() => null);
  const agent = await stopPipecatAgent(sessionId);
  return {
    status: agent.agent_status ?? live?.live?.state ?? agent.status,
    mode: 'pipecat-orchestrated',
    agent,
    transport: {
      provider: 'pipecat',
      configured: agent.connected,
      connect_url: `/sessions/${sessionId}/live/join`,
      live_session: live?.live ?? agent.live_session ?? null,
      instructions: agent.instructions ?? null,
      model: (agent.realtime as Record<string, unknown> | undefined)?.model as string | undefined,
    },
  };
}
