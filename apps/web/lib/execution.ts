import { apiErrorMessage } from './apiError';
import { wayloRequestHeaders, type WayloTargetConnection } from './wayloConnection';

export type ExecutionMode = 'text_callable' | 'voice_fixture' | 'pipecat_webrtc';
export type AudioTransportId =
  | 'waylo_livekit'
  | 'none'
  | 'pipecat_small_webrtc'
  | 'pipecat_daily_webrtc'
  | 'signalwire_webrtc'
  | 'freeswitch_verto_sip';
export type TesterId = 'scenario_simulator' | 'fixture_replay' | 'pipecat_tester';
export type ExecutorId =
  | 'waylo_livekit'
  | 'local_async_runner'
  | 'evidence_replay'
  | 'cae_local_audio_loop'
  | 'pipecat_public_daily'
  | 'signalwire_public_webrtc'
  | 'acc_browser_webrtc'
  | 'acc_sip'
  | 'acc_phone';
export type AgentTarget =
  | 'waylo'
  | 'mock_agent'
  | 'openai_codex'
  | 'offline_acc_fixture'
  | 'voice_fixture'
  | 'builtin_sample_voice'
  | 'pipecat_public_demo'
  | 'signalwire_holy_guacamole'
  | 'sip_agent'
  | 'phone_agent'
  | 'browser_webrtc_agent'
  | 'http_endpoint';

export interface AgentRecord {
  id: string;
  name: string;
  channel: 'text' | 'voice';
  target: AgentTarget;
  environment?: 'local' | 'staging' | 'production';
  connection?: {
    endpoint_url?: string | null;
    auth_type?: 'none' | 'bearer_secret' | 'api_key_secret' | 'waylo_browser_session';
    secret_ref?: string | null;
    api_key_header?: string;
    response_path?: string;
    timeout_ms?: number;
    sip_uri?: string | null;
    phone_number?: string | null;
    acc_base_url?: string | null;
    workspace_id?: string | null;
    waylo_agent_id?: string | null;
  };
  description?: string | null;
  metadata?: {
    model_name?: string | null;
    prompt_version?: string | null;
  };
  created_at?: string;
  updated_at?: string;
}

export interface ProductProjectOption {
  id: string;
  user_id: string;
  workspace_id?: string | null;
  project_id: string;
  name: string;
  plan: 'free' | 'starter' | 'team' | 'business';
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
  word_error_rate?: WordErrorRateSummary | null;
}

export interface WordErrorRateSummary {
  rate: number;
  percent: number;
  errors: number;
  reference_words: number;
  substitutions: number;
  deletions: number;
  insertions: number;
  turn_count?: number;
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
  direction?: 'tester_to_target' | 'target_to_tester' | string | null;
  evidence_role?: string | null;
  frame_metadata?: {
    bytes?: number;
    sample_rate?: number;
    channels?: number;
    duration_ms?: number;
    sent_at?: number;
    response_metric?: string;
    response_latency_ms?: number;
    response_complete_latency_ms?: number;
    [key: string]: unknown;
  };
}

export interface ConversationLiveEvent {
  sequence: number;
  kind: 'message' | 'audio';
  speaker: string;
  text: string;
  media_url?: string | null;
  mime_type?: string | null;
  created_at?: string | null;
}

export interface ConversationRecord {
  evidence_coverage?: Record<string, { status: string; evaluation_status: string; missing: string[] }>;
  configuration_check?: { status: string; reason: string; agent_version?: number };
  conversation_id: string;
  execution_run_id: string;
  suite_id: string;
  scenario_id: string;
  scenario_title?: string | null;
  mode: ExecutionMode;
  status: string;
  iteration?: number;
  turns?: ConversationTurn[];
  live_events?: ConversationLiveEvent[];
  transcript?: string | null;
  action_trace?: Array<Record<string, unknown>>;
  final_state?: Record<string, unknown>;
  evaluation_findings?: Record<string, unknown>;
  judge_reviews?: JudgeReviewRecord[];
  evaluation_adjudication?: EvaluationAdjudication | null;
  recording?: Record<string, unknown> | null;
  vcon_export?: Record<string, unknown> | null;
  vcon_export_summary?: Record<string, unknown> | null;
  ietf_vcon_export?: Record<string, unknown> | null;
  ietf_vcon_export_summary?: Record<string, unknown> | null;
  audio_session?: Record<string, unknown> | null;
  latency_marks?: Array<Record<string, unknown>>;
  metrics_summary?: ConversationMetricsSummary | null;
  timeline?: TimelineEvent[];
  verdict?: string | null;
  score?: number | null;
  error?: string | null;
}

export interface ExecutionRunRecord {
  evidence_source?: string;
  source_benchmark_run_id?: string;
  execution_run_id: string;
  status: string;
  mode: ExecutionMode;
  suite_id: string;
  scenario_ids: string[];
  user_id: string;
  project_id: string;
  product_project_id?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
  model_name?: string | null;
  max_exchanges?: number;
  duplex_timeout_seconds?: number;
  tester_id?: TesterId;
  tester_model_name?: string | null;
  executor_id?: ExecutorId;
  provenance?: ExecutionRunProvenance | null;
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

export interface ExecutionRunProvenance {
  target_id?: string | null;
  target_kind: string;
  target_channel: 'text' | 'voice';
  target_environment?: string;
  tester_id: TesterId;
  executor_id: ExecutorId;
  evidence_source: string;
  evidence_capabilities?: string[];
  live_external_connection: boolean;
  saved_evidence: boolean;
  synthetic_media: boolean;
  honesty_label?: string | null;
}

export interface LlmJudgeResult {
  agrees?: boolean | null;
  rationale?: string | null;
  next_action?: string | null;
  proposed_evaluation?: JudgeProposedEvaluation | null;
  raw_output?: string | null;
  provenance?: AssertJudgeProvenance | null;
}

export interface AssertJudgeProvenance {
  engine?: string;
  assert_version?: string;
  judge_status?: string;
  evidence_level?: 'black_box' | 'partial_structured' | 'gray_box' | string;
  score_keys?: string[];
  not_applicable_score_keys?: string[];
  dimensions?: Record<string, boolean | number | string | null>;
  dimension_applicability?: Record<string, boolean>;
  dimension_justifications?: Record<string, string>;
  dimension_scales?: Record<string, {
    type?: string;
    values?: Array<{ value: number | string; label: string }>;
  }>;
  node_judgments?: Array<Record<string, unknown>>;
  multi_judge?: Record<string, unknown> | null;
  artifacts?: Record<string, string>;
  input_fingerprint?: string;
  score_sha256?: string;
}

export interface JudgeProposedEvaluation {
  verdict: 'pass' | 'needs_review' | 'fail';
  summary: string;
  corrected_findings: string[];
  remaining_gaps: string[];
}

export interface JudgeReviewRecord {
  review_id: string;
  status: 'pending_confirmation' | 'applied' | 'superseded';
  created_at: string;
  applied_at?: string | null;
  applied_by_user_id?: string | null;
  provider?: string | null;
  model?: string | null;
  latency_ms?: number | null;
  evidence_citations?: string[];
  judge_result?: LlmJudgeResult | null;
  output_sha256?: string | null;
}

export interface EvaluationAdjudication {
  review_id: string;
  source: 'llm_judge';
  status: 'applied';
  applied_at: string;
  applied_by_user_id: string;
  provider?: string | null;
  model?: string | null;
  latency_ms?: number | null;
  evidence_citations?: string[];
  judge_result?: LlmJudgeResult | null;
  output_sha256?: string | null;
  deterministic_snapshot?: {
    verdict?: string | null;
    score?: number | null;
    evidence_sha256?: string | null;
  };
}

export interface LlmJudgeResponse {
  execution_run_id?: string;
  conversation_id?: string;
  source_benchmark_run_id?: string;
  reused?: boolean;
  status: 'blocked' | 'ready';
  required_plan: 'free' | 'starter' | 'team';
  credits: number;
  message: string;
  evidence_citations: string[];
  judge_output?: string | null;
  judge_result?: LlmJudgeResult | null;
  provider?: string | null;
  model?: string | null;
  prompt_preview?: string | null;
  latency_ms?: number | null;
  review_id?: string | null;
  block_reason?: 'provider' | 'budget' | 'provider_error' | 'evidence' | null;
  engine?: string | null;
  assert_version?: string | null;
  assert_result?: Record<string, unknown> | null;
  artifacts?: Record<string, string> | null;
  input_fingerprint?: string | null;
  spend_control?: {
    estimated_credits?: number;
    daily_credit_limit?: number;
    reserved_daily_credits?: number;
    spent_daily_credits?: number;
    remaining_daily_credits?: number;
    provider?: string;
    provider_configured?: boolean;
  };
}

export interface AccConnectionStatus {
  connected: boolean;
  status: string;
  label: string;
  message: string;
  base_url?: string | null;
  readiness_url?: string | null;
  destinations?: Record<string, {
    acc_ready?: boolean;
    cae_executor_available?: boolean;
    creatable?: boolean;
    executor_id?: ExecutorId;
    label?: string;
  }>;
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

const BUILT_IN_AGENT_IDS = new Set([
  'mock-text-agent',
  'generalist-text-agent',
  'generalist-voice-agent',
  'pipecat-public-demo',
  'holyguacamole-signalwire-agent',
]);
const BUILT_IN_AGENT_TARGETS = new Set<AgentTarget>(['mock_agent', 'builtin_sample_voice']);

export function isSeedAgent(agent: Pick<AgentRecord, 'id'>) {
  return BUILT_IN_AGENT_IDS.has(agent.id);
}

export function isBuiltInAgent(agent: Pick<AgentRecord, 'id' | 'target'>) {
  return isSeedAgent(agent) || BUILT_IN_AGENT_TARGETS.has(agent.target);
}

export function agentTryItOutHref(agentId: string, apiBase?: string | null) {
  const params = new URLSearchParams({ agent_id: agentId });
  if (apiBase) params.set('api_base', apiBase);
  return `/runs?${params.toString()}`;
}

export function applyAgentLaunchDefaults(
  agent: Pick<AgentRecord, 'channel' | 'target'>,
): {
  mode: ExecutionMode;
  testerId: TesterId;
  executorId: ExecutorId;
  audioTransport: AudioTransportId;
  textCallable?: AgentTarget;
} {
  if (agent.target === 'waylo') {
    return { mode: 'pipecat_webrtc', testerId: 'pipecat_tester', executorId: 'waylo_livekit', audioTransport: 'waylo_livekit' };
  }
  if (agent.target === 'builtin_sample_voice') {
    return {
      mode: 'pipecat_webrtc',
      testerId: 'pipecat_tester',
      executorId: 'cae_local_audio_loop',
      audioTransport: 'pipecat_small_webrtc',
    };
  }
  if (agent.target === 'pipecat_public_demo') {
    return {
      mode: 'pipecat_webrtc',
      testerId: 'pipecat_tester',
      executorId: 'pipecat_public_daily',
      audioTransport: 'pipecat_daily_webrtc',
    };
  }
  if (agent.target === 'signalwire_holy_guacamole') {
    return {
      mode: 'pipecat_webrtc',
      testerId: 'pipecat_tester',
      executorId: 'signalwire_public_webrtc',
      audioTransport: 'signalwire_webrtc',
    };
  }
  if (agent.target === 'voice_fixture') {
    return {
      mode: 'voice_fixture',
      testerId: 'fixture_replay',
      executorId: 'evidence_replay',
      audioTransport: 'none',
    };
  }
  if (agent.target === 'offline_acc_fixture') {
    return {
      mode: 'text_callable',
      testerId: 'fixture_replay',
      executorId: 'evidence_replay',
      audioTransport: 'none',
      textCallable: 'offline_acc_fixture',
    };
  }
  return {
    mode: 'text_callable',
    testerId: 'scenario_simulator',
    executorId: 'local_async_runner',
    audioTransport: 'none',
    textCallable: ['mock_agent', 'openai_codex', 'offline_acc_fixture', 'http_endpoint'].includes(agent.target) ? agent.target : 'mock_agent',
  };
}

export async function listAgents(): Promise<AgentRecord[]> {
  const payload = await handleJson<{ agents?: AgentRecord[] }>(
    await fetch(`${getApiBase()}/api/agents`, { cache: 'no-store' }),
  );
  return payload.agents ?? [];
}

export async function listProductProjects(userId: string): Promise<ProductProjectOption[]> {
  const payload = await handleJson<unknown>(
    await fetch(`${getApiBase()}/api/product/projects?user_id=${encodeURIComponent(userId)}`, { cache: 'no-store' }),
  );
  return Array.isArray(payload) ? payload as ProductProjectOption[] : [];
}

export interface AssertJudgeReadiness {
  engine: 'assert';
  ready: boolean;
  enabled: boolean;
  model: string;
  message: string;
}

export async function getAssertJudgeReadiness(): Promise<AssertJudgeReadiness> {
  return handleJson(await fetch(`${getApiBase()}/api/assert/readiness`, { cache: 'no-store' }));
}

async function requestAssertJudge(path: string, payload: { user_id: string; request_id?: string }): Promise<LlmJudgeResponse> {
  if (!payload.user_id.trim()) throw new Error('A user identity is required for a saved ASSERT review.');
  return handleJson(await fetch(`${getApiBase()}/api/assert/${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }));
}

export async function requestLlmJudge(payload: {
  user_id: string;
  execution_run_id: string;
  conversation_id: string;
  request_id?: string;
}): Promise<LlmJudgeResponse> {
  if (!payload.execution_run_id || !payload.conversation_id) {
    throw new Error('Select a saved conversation before requesting an ASSERT review.');
  }
  return requestAssertJudge(
    `runs/${encodeURIComponent(payload.execution_run_id)}/conversations/${encodeURIComponent(payload.conversation_id)}/judge`,
    { user_id: payload.user_id, request_id: payload.request_id },
  );
}

export async function requestBenchmarkJudge(payload: {
  user_id: string;
  benchmark_run_id: string;
  request_id?: string;
}): Promise<LlmJudgeResponse> {
  if (!payload.benchmark_run_id) throw new Error('Evaluate the uploaded evidence before requesting an ASSERT review.');
  return requestAssertJudge(`benchmarks/${encodeURIComponent(payload.benchmark_run_id)}/judge`,
    { user_id: payload.user_id, request_id: payload.request_id });
}

export async function applyLlmJudgeReview(payload: {
  executionRunId: string;
  conversationId: string;
  reviewId: string;
  userId: string;
}): Promise<ExecutionRunRecord> {
  return handleJson(
    await fetch(
      `${getApiBase()}/api/execution/runs/${encodeURIComponent(payload.executionRunId)}`
      + `/conversations/${encodeURIComponent(payload.conversationId)}`
      + `/judge-reviews/${encodeURIComponent(payload.reviewId)}/apply`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: payload.userId, confirm: true }),
      },
    ),
  );
}

export async function createAgent(payload: {
  name: string;
  channel: AgentRecord['channel'];
  target: AgentRecord['target'];
  environment?: AgentRecord['environment'];
  connection?: AgentRecord['connection'];
  description?: string | null;
}): Promise<AgentRecord> {
  const endpoint = `${getApiBase()}/api/agents`;
  return handleJson(
    await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(payload.target === 'waylo' ? wayloRequestHeaders(payload.connection, endpoint) : {}) },
      body: JSON.stringify(payload),
    }),
  );
}

export async function updateAgent(
  agentId: string,
  payload: Partial<Pick<AgentRecord, 'name' | 'channel' | 'target' | 'environment' | 'connection' | 'description'>>,
): Promise<AgentRecord> {
  const endpoint = `${getApiBase()}/api/agents/${encodeURIComponent(agentId)}`;
  return handleJson(
    await fetch(endpoint, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...(payload.connection?.auth_type === 'waylo_browser_session' ? wayloRequestHeaders(payload.connection, endpoint) : {}) },
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

export async function getAccConnectionStatus(baseUrl?: string): Promise<AccConnectionStatus> {
  const params = baseUrl ? `?base_url=${encodeURIComponent(baseUrl)}` : '';
  return handleJson(await fetch(`${getApiBase()}/api/execution/acc-connection${params}`, { cache: 'no-store' }));
}

export async function testAccConnection(baseUrl: string): Promise<AccConnectionStatus> {
  return handleJson(
    await fetch(`${getApiBase()}/api/execution/acc-connection/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: baseUrl }),
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
  max_exchanges?: number;
  duplex_timeout_seconds?: number;
  user_id: string;
  project_id: string;
  product_project_id?: string;
  agent_id?: string;
  text_callable?: string;
  model_name?: string;
  tester_id?: TesterId;
  tester_model_name?: string;
  executor_id?: ExecutorId;
  evaluate?: boolean;
  audio_transport?: AudioTransportId;
}, connection?: WayloTargetConnection): Promise<ExecutionRunRecord> {
  const endpoint = `${getApiBase()}/api/execution/runs`;
  return handleJson(
    await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(payload.executor_id === 'waylo_livekit' ? wayloRequestHeaders(connection, endpoint) : {}) },
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


export async function downloadAssertHtmlReport(payload: {
  executionRunId: string; conversationId: string; reviewId: string; userId: string;
}): Promise<void> {
  const response = await fetch(
    `${getApiBase()}/api/assert/runs/${encodeURIComponent(payload.executionRunId)}`
    + `/conversations/${encodeURIComponent(payload.conversationId)}`
    + `/reviews/${encodeURIComponent(payload.reviewId)}/report.html?user_id=${encodeURIComponent(payload.userId)}`,
    { cache: 'no-store' },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail || 'Could not export the saved ASSERT report.');
  }
  if (!response.headers.get('content-type')?.startsWith('text/html')) {
    throw new Error('The server did not return an HTML report.');
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  const filename = response.headers.get('content-disposition')?.match(/filename="([a-zA-Z0-9_.-]+)"/)?.[1];
  anchor.href = url;
  const safe = (value: string) => value.replace(/[^a-zA-Z0-9_-]/g, '-').slice(0, 60) || 'unknown';
  anchor.download = filename || `assert-${safe(payload.executionRunId)}-${safe(payload.conversationId)}-${safe(payload.reviewId)}.html`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface AssertReviewFreshness {
  execution_run_id: string; conversation_id: string; review_id: string;
  status: 'current' | 'stale' | 'cannot_verify'; reason_code: string; message: string;
}

export async function getAssertReviewFreshness(payload: {
  executionRunId: string; conversationId: string; reviewId: string; userId: string;
}): Promise<AssertReviewFreshness> {
  return handleJson(await fetch(`${getApiBase()}/api/assert/runs/${encodeURIComponent(payload.executionRunId)}`
    + `/conversations/${encodeURIComponent(payload.conversationId)}/reviews/${encodeURIComponent(payload.reviewId)}`
    + `/status?user_id=${encodeURIComponent(payload.userId)}`, { cache: 'no-store' }));
}
