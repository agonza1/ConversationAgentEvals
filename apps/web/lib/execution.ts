import { apiErrorMessage } from './apiError';

export type ExecutionMode = 'text_callable' | 'voice_fixture' | 'pipecat_webrtc';
export type AudioTransportId = 'none' | 'pipecat_small_webrtc' | 'freeswitch_verto_sip';

export interface AgentRecord {
  id: string;
  name: string;
  channel: 'text' | 'voice';
  target: 'mock_agent' | 'openai_codex' | 'offline_acc_fixture' | 'voice_fixture' | 'http_endpoint';
  environment?: 'local' | 'staging' | 'production';
  connection?: {
    endpoint_url?: string | null;
    auth_type?: 'none' | 'bearer_secret' | 'api_key_secret';
    secret_ref?: string | null;
    api_key_header?: string;
    response_path?: string;
    timeout_ms?: number;
  };
  description?: string | null;
  metadata?: {
    model_name?: string | null;
    prompt_version?: string | null;
  };
  created_at?: string;
  updated_at?: string;
}

export interface LatencyStats {
  count: number;
  avg_ms?: number | null;
  median_ms?: number | null;
  p90_ms?: number | null;
  min_ms?: number | null;
  max_ms?: number | null;
  outlier_count: number;
}

export interface ConversationMetricsSummary {
  verdict?: string | null;
  score?: number | null;
  turn_count: number;
  latency: LatencyStats;
  interruption_count: number;
  call_resolution_success: number;
}

export interface TimelineEvent {
  t_ms?: number | null;
  label: string;
  latency_ms?: number | null;
  kind: string;
}

export interface ConversationTurn {
  turn_index: number;
  speaker?: string | null;
  text?: string | null;
  latency_ms?: number | null;
  event_types?: string[];
}

export interface ConversationRecord {
  conversation_id: string;
  execution_run_id: string;
  suite_id: string;
  scenario_id: string;
  scenario_title?: string | null;
  mode: ExecutionMode;
  status: string;
  iteration?: number;
  turns?: ConversationTurn[];
  transcript?: string | null;
  latency_marks?: Array<Record<string, unknown>>;
  metrics_summary?: ConversationMetricsSummary | null;
  timeline?: TimelineEvent[];
  verdict?: string | null;
  score?: number | null;
  error?: string | null;
}

export interface ExecutionRunRecord {
  execution_run_id: string;
  status: string;
  mode: ExecutionMode;
  suite_id: string;
  scenario_ids: string[];
  user_id: string;
  project_id: string;
  agent_id?: string | null;
  agent_name?: string | null;
  model_name?: string | null;
  tester_id?: 'scenario_simulator' | 'fixture_replay' | 'pipecat_tester';
  tester_model_name?: string | null;
  executor_id?: 'local_async_runner';
  execution_snapshot?: Record<string, unknown> | null;
  progress: {
    phase: string;
    completed_conversations: number;
    total_conversations: number;
    percent: number;
    active_conversation_id?: string | null;
  };
  conversations: ConversationRecord[];
  inference_set_path?: string | null;
  run_snapshot_path?: string | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

function normalizeApiBase(value: string) {
  return value.replace(/\/$/, '').replace(/\/api$/, '');
}

export function getApiBase() {
  if (typeof window === 'undefined') {
    return normalizeApiBase(process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8025');
  }
  const fromQuery = new URLSearchParams(window.location.search).get('api_base');
  if (fromQuery) {
    try {
      return normalizeApiBase(new URL(fromQuery, window.location.origin).toString());
    } catch {
      // Fall through.
    }
  }
  return '';
}

async function handleJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  if (!response.ok) {
    throw new Error(apiErrorMessage(text, response.status));
  }
  return (text ? JSON.parse(text) : {}) as T;
}

const BUILT_IN_AGENT_IDS = new Set(['mock-text-agent', 'acc-voice-fixture-agent']);

export function isBuiltInAgent(agent: Pick<AgentRecord, 'id'>) {
  return BUILT_IN_AGENT_IDS.has(agent.id);
}

export function agentTryItOutHref(agentId: string, apiBase?: string | null) {
  const params = new URLSearchParams({ launch: 'demo', agent_id: agentId });
  if (apiBase) params.set('api_base', apiBase);
  return `/runs?${params.toString()}`;
}

export function applyAgentLaunchDefaults(
  agent: Pick<AgentRecord, 'channel' | 'target'>,
): {
  mode: ExecutionMode;
  textCallable?: AgentRecord['target'];
} {
  if (agent.channel === 'voice' || agent.target === 'voice_fixture' || agent.target === 'offline_acc_fixture') {
    return { mode: 'voice_fixture' };
  }
  return {
    mode: 'text_callable',
    textCallable: ['mock_agent', 'openai_codex', 'offline_acc_fixture', 'http_endpoint'].includes(agent.target) ? agent.target : 'mock_agent',
  };
}

export async function listAgents(): Promise<AgentRecord[]> {
  const payload = await handleJson<{ agents?: AgentRecord[] }>(
    await fetch(`${getApiBase()}/api/agents`, { cache: 'no-store' }),
  );
  return payload.agents ?? [];
}

export async function createAgent(payload: {
  name: string;
  channel: AgentRecord['channel'];
  target: AgentRecord['target'];
  environment?: AgentRecord['environment'];
  connection?: AgentRecord['connection'];
  description?: string | null;
}): Promise<AgentRecord> {
  return handleJson(
    await fetch(`${getApiBase()}/api/agents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

export async function updateAgent(
  agentId: string,
  payload: Partial<Pick<AgentRecord, 'name' | 'channel' | 'target' | 'environment' | 'connection' | 'description'>>,
): Promise<AgentRecord> {
  return handleJson(
    await fetch(`${getApiBase()}/api/agents/${encodeURIComponent(agentId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

export async function deleteAgent(agentId: string): Promise<void> {
  await handleJson(
    await fetch(`${getApiBase()}/api/agents/${encodeURIComponent(agentId)}`, {
      method: 'DELETE',
    }),
  );
}

export async function listExecutionRuns(userId: string, projectId?: string): Promise<ExecutionRunRecord[]> {
  const params = new URLSearchParams({ user_id: userId });
  if (projectId) params.set('project_id', projectId);
  return handleJson(await fetch(`${getApiBase()}/api/execution/runs?${params}`, { cache: 'no-store' }));
}

export async function getExecutionRun(userId: string, executionRunId: string): Promise<ExecutionRunRecord> {
  return handleJson(
    await fetch(
      `${getApiBase()}/api/execution/runs/${encodeURIComponent(executionRunId)}?user_id=${encodeURIComponent(userId)}`,
      { cache: 'no-store' },
    ),
  );
}

export async function createExecutionRun(payload: {
  suite_id: string;
  scenario_ids?: string[];
  mode?: ExecutionMode;
  iterations?: number;
  user_id: string;
  project_id: string;
  agent_id?: string;
  text_callable?: string;
  model_name?: string;
  tester_id?: 'scenario_simulator' | 'fixture_replay' | 'pipecat_tester';
  tester_model_name?: string;
  executor_id?: 'local_async_runner';
  evaluate?: boolean;
  audio_transport?: AudioTransportId;
}): Promise<ExecutionRunRecord> {
  return handleJson(
    await fetch(`${getApiBase()}/api/execution/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

export function demoUserId() {
  if (typeof window === 'undefined') return 'demo-user';
  const existing = window.localStorage.getItem('conversation-evals-demo-user');
  if (existing) return existing;
  const next = `demo-user-${Math.random().toString(36).slice(2, 8)}`;
  window.localStorage.setItem('conversation-evals-demo-user', next);
  return next;
}

export function demoProjectId() {
  if (typeof window === 'undefined') return 'call-center-demo';
  const existing = window.localStorage.getItem('conversation-evals-demo-project');
  if (existing) return existing;
  window.localStorage.setItem('conversation-evals-demo-project', 'call-center-demo');
  return 'call-center-demo';
}
