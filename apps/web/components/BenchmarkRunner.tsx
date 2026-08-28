'use client';

import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import { ApiAwareLink } from './ApiAwareLink';
import { LiveRunFeedback, type LiveRunEvent } from './LiveRunFeedback';
import { apiErrorMessage } from '@/lib/apiError';
import { listProductProjects, type ProductProjectOption } from '@/lib/execution';

type JsonRecord = Record<string, unknown>;

interface BenchmarkSuite {
  id: string;
  title: string;
  description?: string | null;
  scenarios: BenchmarkScenario[];
}

interface BenchmarkSuiteContractManifest {
  suite_id: string;
  suite_name?: string;
  provider?: string;
  scenario_count?: number;
  suite_contract_manifest_sha256?: string;
  scenario_contracts?: Array<{
    scenario_id?: string;
    scenario_title?: string;
    scenario_contract_sha256?: string;
  }>;
  evidence_requirements?: {
    required_artifacts?: string[];
    optional_artifacts?: string[];
    scoring_dimensions?: string[];
  };
}

interface BenchmarkScenario {
  id: string;
  suite_id?: string;
  title: string;
  domain?: string | null;
  user_persona?: string | null;
  user_goal?: string | null;
  constraints?: string[] | string | null;
  required_actions?: string[] | string | null;
  forbidden_actions?: string[] | string | null;
  expected_final_state?: JsonRecord | string | null;
  rubric?: string[] | string | null;
  sample_transcript?: string | null;
  sample_action_trace?: unknown;
  sample_final_state?: unknown;
}

interface BenchmarkReport {
  run_id?: string;
  suite_id?: string;
  scenario_id?: string;
  verdict?: string;
  overall?: string;
  scenario_title?: string;
  suite_contract_manifest_sha256?: string;
  scenario_contract_sha256?: string;
  score?: number;
  overall_score?: number;
  scoring_mode?: 'transcript' | 'agentic' | string;
  score_components?: Record<string, number>;
  task_completion_score?: number | null;
  required_action_score?: number;
  rubric_score?: number;
  forbidden_action_score?: number | null;
  evidence_citations?: Array<string | JsonRecord>;
  final_state_score?: number | null;
  workflow_order_score?: number | null;
  completed_actions?: string[];
  evidence_spans?: Array<string | JsonRecord>;
  evidence?: Array<string | JsonRecord>;
  missing_actions?: string[];
  forbidden_action_hits?: Array<string | JsonRecord>;
  forbidden_actions_observed?: string[];
  failure_categories?: string[];
  recommendations?: string[];
  suggested_fixes?: string[];
  transcript?: string;
  action_trace?: unknown;
  final_state?: unknown;
  run_metadata?: RunMetadata;
  run_lifecycle?: RunLifecycle;
  evidence_audit_summary?: EvidenceAuditSummary;
  group_call_summary?: GroupCallSummary | null;
  voice_interaction_summary?: VoiceInteractionSummary | null;
  vcon_analysis?: JsonRecord;
  vcon_export?: JsonRecord;
  simulation_validation?: SimulationValidation;
  llm_judge?: JsonRecord;
}

interface SimulationValidation {
  status?: 'ready_for_scoring' | 'needs_regeneration' | string;
  ready_for_scoring?: boolean;
  artifact_presence?: {
    transcript?: boolean;
    action_trace?: boolean;
    final_state?: boolean;
  };
  missing_required_actions?: string[];
  completed_required_action_count?: number;
  final_state_complete?: boolean;
}

interface GroupCallSummary {
  speaker_count?: number;
  speakers?: string[];
  message_count?: number;
  decision_count?: number;
  commitment_count?: number;
  follow_up_count?: number;
  action_item_count?: number;
}

interface VoiceInteractionSummary {
  turn_count?: number;
  interruption_signal_count?: number;
  correction_signal_count?: number;
  handoff_signal_count?: number;
  action_trace_event_count?: number;
  duration_ms?: number;
  average_latency_ms?: number;
  max_latency_ms?: number;
  packet_loss_percent?: number;
  jitter_ms?: number;
  media?: {
    recording_url?: string;
    recording_sha256?: string;
    mime_type?: string;
    duration_ms?: number;
  };
}

interface RunLifecycle {
  attempt?: number;
  retry_of_run_id?: string | null;
  retryable?: boolean;
  next_attempt?: number;
  transitions?: Array<{ to?: string; at?: string; reason?: string }>;
}

interface RunMetadata {
  agent_version?: string;
  prompt_version?: string;
  model_name?: string;
  notes?: string;
  user_id?: string;
  project_id?: string;
}

interface EvidenceAuditSummary {
  run_started_at?: string;
  evaluated_at?: string;
  input_artifact_types?: string[];
  transcript_present?: boolean;
  action_trace_present?: boolean;
  final_state_present?: boolean;
  metadata_labels?: string[];
  evaluator_version?: string;
  export_readiness?: {
    ready?: boolean;
    format?: string;
    missing?: string[];
  };
}

interface RegressionDelta {
  status?: 'baseline' | 'improved' | 'regressed' | 'unchanged' | string;
  previous_run_id?: string | null;
  previous_overall_score?: number | null;
  current_overall_score?: number | null;
  score_delta?: number | null;
}

interface SavedRunArtifacts {
  overall_score?: number;
  transcript_lines?: number;
  has_transcript?: boolean;
  evidence_items?: number;
  regression_delta?: RegressionDelta;
  audit_artifacts?: SavedRunAuditArtifactSummary;
  contract_artifacts?: SavedRunContractArtifactSummary;
  vcon_export?: SavedRunVconExportSummary;
}

interface SavedRunAuditArtifactSummary {
  available?: boolean;
  ready_for_export?: boolean;
  artifact_types?: string[];
  missing?: string[];
  evaluator_version?: string | null;
  classification?: 'assert' | 'unsupported' | string;
  active_evaluator_input?: boolean;
}

interface SavedRunContractArtifactSummary {
  available?: boolean;
  suite_contract_manifest_sha256?: string | null;
  scenario_contract_sha256?: string | null;
}

interface SavedRunVconExportSummary {
  available?: boolean;
  dialog_turns?: number;
  analysis_count?: number;
  source_format?: string | null;
  appended_analysis_type?: string | null;
}

interface ProjectContractArtifactSummary {
  available_records?: number;
  missing_records?: number;
  total_runs?: number;
  suite_contract_manifest_sha256s?: string[];
  scenario_contract_sha256s?: string[];
}

interface ScenarioCoverageSummary {
  suite_id?: string | null;
  scenario_count?: number | null;
  covered_scenario_count?: number;
  coverage_percent?: number | null;
  covered_scenario_ids?: string[];
  missing_scenario_ids?: string[];
  out_of_suite_scenario_ids?: string[];
  covered_scenarios?: Array<{ id?: string; title?: string }>;
  missing_scenarios?: Array<{ id?: string; title?: string }>;
  out_of_suite_scenarios?: Array<{ id?: string; title?: string }>;
  recommended_next_scenario?: { id?: string; title?: string } | null;
  coverage_status?: 'empty' | 'partial' | 'complete' | string;
}

interface PricingPlan {
  id: 'free' | 'starter' | 'team' | 'business';
  name: string;
  price_label: string;
  seats: string;
  included_credits?: number | null;
  cta: string;
  features: string[];
}

interface UsageRule {
  id: string;
  label: string;
  credits: number;
  gated_plan?: PricingPlan['id'] | null;
}

interface ProductConfig {
  pricing: PricingPlan[];
  usage_rules: UsageRule[];
  auth: {
    enabled: boolean;
    mode: 'configured' | 'placeholder';
    providers: string[];
    project_id?: string | null;
    api_key_configured: boolean;
  };
  voice_status: 'planned' | 'gated' | 'enabled';
  llm_judge_status: 'planned' | 'gated' | 'enabled';
}

interface OpenAIProviderStatus {
  id?: string;
  provider?: string;
  status: 'connected' | 'disconnected' | 'expired' | string;
  email?: string | null;
  account_id?: string | null;
  plan_type?: string | null;
  message?: string | null;
  last_error?: string | null;
}

const DEFAULT_EXECUTION_MODEL = 'gpt-5.4-mini';
const LOCAL_EXECUTION_MODELS = ['ollama/gemma2:2b'];
const FALLBACK_EXECUTION_MODELS = [
  'gpt-5.4-mini',
  ...LOCAL_EXECUTION_MODELS,
  'gpt-5.4',
  'gpt-5.2',
  'gpt-4.1',
  'gpt-4.1-mini',
  'gpt-4o',
  'o3',
  'o3-mini',
  'o4-mini',
];

async function fetchOpenAIModels(): Promise<{ models: string[]; message: string | null }> {
  const response = await fetch(`${getApiBase()}/api/product/providers/openai/models`, { cache: 'no-store' });
  if (response.status === 401) {
    return { models: [DEFAULT_EXECUTION_MODEL, ...LOCAL_EXECUTION_MODELS], message: 'Connect OpenAI to load GPT models; local Ollama models stay available.' };
  }
  if (!response.ok) {
    // Never leave the dropdown empty on transient API failures.
    return {
      models: FALLBACK_EXECUTION_MODELS,
      message: 'Using built-in model list. Re-connect OpenAI to refresh.',
    };
  }
  const payload = await handleJson<{
    models?: Array<{ id?: string } | string>;
    default_model?: string;
    message?: string | null;
    source?: string;
  }>(response);
  const ids = (payload.models ?? [])
    .map((item) => (typeof item === 'string' ? item : item.id))
    .filter((id): id is string => Boolean(id && id.trim()));
  const merged = Array.from(new Set([DEFAULT_EXECUTION_MODEL, ...LOCAL_EXECUTION_MODELS, ...ids]));
  merged.sort((a, b) => {
    if (a === DEFAULT_EXECUTION_MODEL) return -1;
    if (b === DEFAULT_EXECUTION_MODEL) return 1;
    return a.localeCompare(b);
  });
  return {
    models: merged.length ? merged : FALLBACK_EXECUTION_MODELS,
    message: typeof payload.message === 'string' && payload.message.trim() ? payload.message : null,
  };
}

interface SavedRun {
  id: string;
  project_id: string;
  firestore_path: string;
  plan: PricingPlan['id'];
  report: BenchmarkReport;
  artifacts?: SavedRunArtifacts;
  transcript?: string | null;
  created_at: string;
}

interface ProjectRegressionSummary {
  run_count: number;
  latest_run_id?: string | null;
  latest_score?: number | null;
  previous_score?: number | null;
  latest_delta?: number | null;
  latest_status: RegressionDelta['status'];
  best_score?: number | null;
  worst_score?: number | null;
  average_score?: number | null;
  passing_runs?: number;
  failing_runs?: number;
  pass_rate?: number | null;
  scenario_summaries?: ScenarioRegressionSummary[];
  failure_category_summary?: FailureCategorySummary[];
}

interface FailureCategorySummary {
  category: string;
  count: number;
  latest_run_id?: string | null;
}

interface ScenarioRegressionSummary {
  suite_id?: string | null;
  scenario_id: string;
  run_count: number;
  latest_run_id?: string | null;
  latest_score?: number | null;
  previous_score?: number | null;
  latest_delta?: number | null;
  latest_status: RegressionDelta['status'];
  passing_runs?: number;
  failing_runs?: number;
  pass_rate?: number | null;
}

interface ProjectVconExportSummary {
  available_records: number;
  missing_records: number;
  total_runs: number;
  dialog_turns: number;
  analysis_records: number;
}

interface SavedRunExport {
  id: string;
  filename: string;
  project_id: string;
  firestore_path: string;
  report: BenchmarkReport;
  artifacts?: SavedRunArtifacts;
  transcript?: string | null;
  created_at: string;
}

interface BenchmarkRunAuditArtifactExport {
  filename: string;
  run_id: string;
  [key: string]: unknown;
}

interface ProjectHistoryExport {
  id: string;
  filename: string;
  user_id: string;
  project_id: string;
  project_name: string;
  suite_id?: string | null;
  scenario_id?: string | null;
  firestore_collection_path: string;
  run_count: number;
  summary: ProjectRegressionSummary;
  vcon_export_summary: ProjectVconExportSummary;
  contract_artifact_summary?: ProjectContractArtifactSummary;
  scenario_coverage_summary?: ScenarioCoverageSummary;
  runs: SavedRunExport[];
  exported_at: string;
}

interface BenchmarkRunHistoryExport {
  id: string;
  filename: string;
  user_id: string;
  project_id?: string | null;
  suite_id?: string | null;
  scenario_id?: string | null;
  status?: string | null;
  run_count: number;
  summary: {
    latest_run_id?: string | null;
    latest_status?: string | null;
    latest_score?: number | null;
    previous_score?: number | null;
    latest_delta?: number | null;
    latest_trend?: string | null;
    best_score?: number | null;
    worst_score?: number | null;
    average_score?: number | null;
    status_counts?: Record<string, number>;
    failure_category_counts?: Record<string, number>;
    top_failure_categories?: Array<{ category: string; count: number }>;
  };
  scenario_coverage_summary?: ScenarioCoverageSummary;
  vcon_export_summary: ProjectVconExportSummary;
  contract_artifact_summary?: ProjectContractArtifactSummary;
  runs: JsonRecord[];
  exported_at: string;
}

interface BenchmarkSuiteRunHistoryExport {
  id: string;
  filename: string;
  user_id: string;
  project_id?: string | null;
  suite_id?: string | null;
  status?: string | null;
  suite_run_count: number;
  summary: {
    latest_suite_run_id?: string | null;
    latest_status?: string | null;
    latest_average_score?: number | null;
    previous_average_score?: number | null;
    latest_delta?: number | null;
    latest_trend?: string | null;
    best_average_score?: number | null;
    worst_average_score?: number | null;
    average_score?: number | null;
    status_counts?: Record<string, number>;
    total_scenarios?: number;
    total_passes?: number;
    total_needs_review?: number;
    pass_rate?: number | null;
    failure_category_counts?: Record<string, number>;
    top_failure_categories?: Array<{ category: string; count: number }>;
  };
  vcon_export_summary: ProjectVconExportSummary;
  suite_contract_artifact_summary?: ProjectContractArtifactSummary;
  scenario_coverage_summary?: ScenarioCoverageSummary;
  suite_runs: BenchmarkSuiteRunRecord[];
  exported_at: string;
}

interface BenchmarkSuiteAuditArtifactExport {
  id: string;
  suite_run_id: string;
  suite_id?: string | null;
  filename: string;
  operator_summary?: {
    ready_for_export?: boolean;
    ready_scenarios?: number;
    missing_scenarios?: number;
  };
  scenario_artifacts?: JsonRecord[];
  [key: string]: unknown;
}

interface BenchmarkSuiteVconBundleExport {
  id: string;
  suite_run_id: string;
  suite_id?: string | null;
  suite_name?: string | null;
  user_id?: string | null;
  project_id?: string | null;
  filename: string;
  record_count: number;
  records: JsonRecord[];
  exported_at: string;
}

interface JudgeStructuredResult {
  agrees?: boolean | null;
  rationale?: string | null;
  next_action?: string | null;
  raw_output?: string | null;
}

interface JudgeGate {
  status: 'blocked' | 'ready';
  required_plan: PricingPlan['id'];
  credits: number;
  message: string;
  evidence_citations: string[];
  judge_output?: string | null;
  judge_result?: JudgeStructuredResult | null;
  provider?: string | null;
  model?: string | null;
  prompt_preview?: string | null;
  latency_ms?: number | null;
  block_reason?: 'provider' | 'budget' | 'provider_error' | null;
  spend_control?: {
    estimated_credits?: number;
    daily_credit_limit?: number;
    reserved_daily_credits?: number;
    spent_daily_credits?: number;
    remaining_daily_credits?: number;
    provider?: string;
    provider_configured?: boolean;
    oauth_connected?: boolean;
    api_key_configured?: boolean;
    within_budget?: boolean;
  };
}

interface ProductAuditEvent {
  id: string;
  event_type: string;
  payload: JsonRecord;
  created_at: string;
}

interface BenchmarkSimulationResponse {
  suite_id?: string;
  suite_name?: string;
  scenario_id?: string;
  scenario_title?: string;
  transcript: string;
  action_trace: unknown;
  final_state: unknown;
  run_metadata?: RunMetadata;
  simulation_validation?: SimulationValidation;
  benchmark_report: BenchmarkReport;
}

interface BenchmarkSuiteScenarioSummary {
  scenario_id?: string;
  run_id?: string;
  status?: string;
  overall_score?: number;
  failure_categories?: string[];
}

interface SuiteReliabilityMetrics {
  framework?: string;
  scenario_count?: number;
  attempt_count?: number;
  pass_at_1?: number;
  pass_at_k?: number;
  pass_all_k?: number;
  accuracy_score?: number;
  experience_signal_coverage?: number;
  average_turn_count?: number;
  interruption_signal_count?: number;
  correction_signal_count?: number;
  handoff_signal_count?: number;
  perturbation_tags?: string[];
  perturbation_coverage?: Array<{ tag?: string; scenario_count?: number; pass_count?: number; pass_rate?: number }>;
}

interface BenchmarkSuiteRunProgress {
  phase?: string;
  active?: boolean;
  completed_scenarios?: number;
  total_scenarios?: number;
  percent?: number;
}

interface BenchmarkSuiteRunRecord {
  suite_run_id: string;
  suite_id: string;
  status: string;
  scenario_count: number;
  pass_count: number;
  needs_review_count: number;
  average_score: number;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
  suite_report?: {
    suite_run_id?: string;
    suite_id?: string;
    suite_name?: string;
    provider?: string;
    scenario_count?: number;
    pass_count?: number;
    needs_review_count?: number;
    average_score?: number;
    verdict?: string;
    run_metadata?: RunMetadata;
    reliability_metrics?: SuiteReliabilityMetrics;
    scenario_runs?: BenchmarkSimulationResponse[];
    vcon_export?: JsonRecord;
  };
  run_lifecycle?: { status?: string; terminal?: boolean; transitions?: Array<{ to?: string; at?: string; reason?: string }> };
  progress?: BenchmarkSuiteRunProgress;
  reliability_metrics?: SuiteReliabilityMetrics;
  retention?: { retained_until?: string | null; retention_days?: number; policy?: string };
  artifacts?: {
    scenario_summaries?: BenchmarkSuiteScenarioSummary[];
    vcon_export?: { available?: boolean; dialog_turns?: number; analysis_count?: number; source_format?: string; appended_analysis_type?: string | null };
  };
}

interface BenchmarkSuiteSimulationResponse {
  suite_run_id: string;
  suite_id: string;
  suite_name?: string;
  provider?: string;
  scenario_count: number;
  pass_count: number;
  needs_review_count: number;
  average_score: number;
  verdict: string;
  run_metadata?: RunMetadata;
  reliability_metrics?: SuiteReliabilityMetrics;
  scenario_runs: BenchmarkSimulationResponse[];
  vcon_export?: JsonRecord;
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
      // Fall through to the same-origin API proxy.
    }
  }

  return '';
}

function executionAnalysisHref(executionRunId: string) {
  const params = new URLSearchParams();
  if (typeof window !== 'undefined') {
    const apiBase = new URLSearchParams(window.location.search).get('api_base');
    if (apiBase) params.set('api_base', apiBase);
  }
  const query = params.toString();
  return `/runs/${executionRunId}${query ? `?${query}` : ''}`;
}

async function handleJson<T>(response: Response): Promise<T> {
  const text = await response.text();

  if (!response.ok) {
    throw new Error(apiErrorMessage(text, response.status));
  }

  return (text ? JSON.parse(text) : {}) as T;
}

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

function toStringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item)).filter(Boolean);
  }
  if (typeof value === 'string') {
    return value.split(/\n|;/).map((item) => item.trim()).filter(Boolean);
  }
  return [];
}

function stringifyEditable(value: unknown, fallback = '') {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

function isBlankJsonField(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return true;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (parsed === null) return true;
    if (Array.isArray(parsed)) return parsed.length === 0;
    if (typeof parsed === 'object') return Object.keys(parsed as JsonRecord).length === 0;
    return false;
  } catch {
    return false;
  }
}

function parseMaybeJson(value: string): string | JsonRecord | unknown[] {
  try {
    return JSON.parse(value) as JsonRecord | unknown[];
  } catch {
    return value;
  }
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function normalizeScenario(value: unknown, suiteId?: string): BenchmarkScenario {
  const record = asRecord(value);
  return {
    id: String(record.id ?? record.scenario_id ?? crypto.randomUUID()),
    suite_id: String(record.suite_id ?? suiteId ?? ''),
    title: String(record.title ?? record.name ?? 'Untitled scenario'),
    domain: typeof record.domain === 'string' ? record.domain : null,
    user_persona: typeof record.user_persona === 'string' ? record.user_persona : typeof record.persona === 'string' ? record.persona : null,
    user_goal: typeof record.user_goal === 'string' ? record.user_goal : typeof record.goal === 'string' ? record.goal : null,
    constraints: record.constraints as BenchmarkScenario['constraints'],
    required_actions: record.required_actions as BenchmarkScenario['required_actions'],
    forbidden_actions: record.forbidden_actions as BenchmarkScenario['forbidden_actions'],
    expected_final_state: record.expected_final_state as BenchmarkScenario['expected_final_state'],
    rubric: record.rubric as BenchmarkScenario['rubric'],
    sample_transcript: typeof record.sample_transcript === 'string' ? record.sample_transcript : null,
    sample_action_trace: record.sample_action_trace,
    sample_final_state: record.sample_final_state,
  };
}

function normalizeSuites(payload: unknown): BenchmarkSuite[] {
  const record = asRecord(payload);
  const rawSuites = Array.isArray(payload) ? payload : Array.isArray(record.suites) ? record.suites : [];

  return rawSuites.map((item) => {
    const suite = asRecord(item);
    const id = String(suite.id ?? suite.suite_id ?? crypto.randomUUID());
    const rawScenarios = [
      ...(Array.isArray(suite.scenarios) ? suite.scenarios : []),
      ...(Array.isArray(suite.optional_scenarios) ? suite.optional_scenarios : []),
    ];
    const scenarios = rawScenarios.map((scenario) => normalizeScenario(scenario, id));

    return {
      id,
      title: String(suite.title ?? suite.name ?? 'Untitled suite'),
      description: typeof suite.description === 'string' ? suite.description : null,
      scenarios,
    };
  });
}

async function fetchBenchmarkSuites(signal?: AbortSignal): Promise<BenchmarkSuite[]> {
  const suites = await handleJson<unknown>(
    await fetch(`${getApiBase()}/api/benchmarks/suites`, { cache: 'no-store', signal }),
  );
  const normalizedSuites = normalizeSuites(suites);

  return Promise.all(
    normalizedSuites.map(async (suite) => {
      if (suite.scenarios.length) return suite;

      try {
        const payload = await handleJson<unknown>(
          await fetch(`${getApiBase()}/api/benchmarks/suites/${encodeURIComponent(suite.id)}/scenarios`, { cache: 'no-store' }),
        );
        const record = asRecord(payload);
        const rawScenarios = Array.isArray(payload) ? payload : Array.isArray(record.scenarios) ? record.scenarios : [];
        return { ...suite, scenarios: rawScenarios.map((scenario) => normalizeScenario(scenario, suite.id)) };
      } catch {
        return suite;
      }
    }),
  );
}

async function fetchBenchmarkSuiteContractManifest(suiteId: string) {
  return handleJson<BenchmarkSuiteContractManifest>(
    await fetch(`${getApiBase()}/api/benchmarks/suites/${encodeURIComponent(suiteId)}/contract-manifest`, { cache: 'no-store' }),
  );
}

async function runBenchmark(payload: {
  suite_id: string;
  scenario_id: string;
  transcript: string;
  action_trace?: unknown;
  final_state?: unknown;
  call?: string | JsonRecord | unknown[];
  group_call?: string | JsonRecord | unknown[];
  vcon?: JsonRecord;
  agent_version?: string;
  prompt_version?: string;
  model_name?: string;
  notes?: string;
  user_id?: string;
  project_id?: string;
  attempt?: number;
  max_attempts?: number;
  retry_of_run_id?: string;
}) {
  return handleJson<BenchmarkReport>(
    await fetch(`${getApiBase()}/api/benchmarks/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

async function simulateBenchmark(payload: {
  suite_id: string;
  scenario_id: string;
  agent_profile?: string;
  include_failure?: boolean;
  agent_version?: string;
  prompt_version?: string;
  model_name?: string;
  notes?: string;
  user_id?: string;
  project_id?: string;
}) {
  return handleJson<BenchmarkSimulationResponse>(
    await fetch(`${getApiBase()}/api/benchmarks/simulate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

async function simulateBenchmarkSuite(payload: {
  suite_id: string;
  agent_profile?: string;
  include_failure?: boolean;
  agent_version?: string;
  prompt_version?: string;
  model_name?: string;
  notes?: string;
  user_id?: string;
  project_id?: string;
}) {
  return handleJson<BenchmarkSuiteSimulationResponse>(
    await fetch(`${getApiBase()}/api/benchmarks/suites/${encodeURIComponent(payload.suite_id)}/simulate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

async function enqueueBenchmarkSuiteSimulation(payload: {
  suite_id: string;
  agent_profile?: string;
  include_failure?: boolean;
  agent_version?: string;
  prompt_version?: string;
  model_name?: string;
  notes?: string;
  user_id?: string;
  project_id?: string;
}) {
  return handleJson<BenchmarkSuiteRunRecord>(
    await fetch(`${getApiBase()}/api/benchmarks/suites/${encodeURIComponent(payload.suite_id)}/simulate-async`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

async function saveBenchmarkRun(payload: {
  user_id: string;
  project_id: string;
  plan: PricingPlan['id'];
  report: BenchmarkReport;
  transcript: string;
}) {
  return handleJson<SavedRun>(
    await fetch(`${getApiBase()}/api/product/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

async function listBenchmarkSuiteRuns(userId: string, projectId: string, suiteId?: string, status?: string) {
  const params = new URLSearchParams({ user_id: userId, project_id: projectId });
  if (suiteId) params.set('suite_id', suiteId);
  if (status) params.set('status', status);

  return handleJson<BenchmarkSuiteRunRecord[]>(
    await fetch(`${getApiBase()}/api/benchmarks/suite-runs?${params.toString()}`, { cache: 'no-store' }),
  );
}

async function fetchBenchmarkSuiteRun(userId: string, suiteRunId: string) {
  return handleJson<BenchmarkSuiteRunRecord>(
    await fetch(`${getApiBase()}/api/benchmarks/suite-runs/${encodeURIComponent(suiteRunId)}?user_id=${encodeURIComponent(userId)}`, { cache: 'no-store' }),
  );
}

interface ExecutionConversationTurn {
  turn_index: number;
  speaker?: string | null;
  text?: string | null;
  act_id?: string | null;
  event_types?: string[];
  latency_ms?: number | null;
}

interface ExecutionConversationRecord {
  conversation_id: string;
  execution_run_id: string;
  suite_id: string;
  scenario_id: string;
  scenario_title?: string | null;
  mode: 'text_callable' | 'voice_fixture' | 'pipecat_webrtc';
  status: 'queued' | 'running' | 'completed' | 'needs_review' | 'failed';
  iteration?: number;
  turns?: ExecutionConversationTurn[];
  live_events?: LiveRunEvent[];
  transcript?: string | null;
  latency_marks?: Array<JsonRecord>;
  recording?: JsonRecord | null;
  vcon_export?: JsonRecord | null;
  vcon_export_summary?: JsonRecord | null;
  audio_session?: JsonRecord | null;
  verdict?: string | null;
  score?: number | null;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

interface ExecutionRunRecord {
  execution_run_id: string;
  status: 'queued' | 'running' | 'completed' | 'needs_review' | 'failed';
  mode: 'text_callable' | 'voice_fixture' | 'pipecat_webrtc';
  suite_id: string;
  scenario_ids: string[];
  user_id: string;
  project_id: string;
  progress: {
    phase: string;
    completed_conversations: number;
    total_conversations: number;
    percent: number;
    active_conversation_id?: string | null;
  };
  conversations: ExecutionConversationRecord[];
  inference_set_path?: string | null;
  run_snapshot_path?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
  model_name?: string | null;
  max_exchanges?: number;
  duplex_timeout_seconds?: number;
  tester_id?: 'scenario_simulator' | 'fixture_replay' | 'pipecat_tester';
  tester_model_name?: string | null;
  executor_id?: 'local_async_runner' | 'evidence_replay' | 'cae_local_audio_loop' | 'pipecat_public_daily' | 'signalwire_public_webrtc' | 'acc_browser_webrtc' | 'acc_sip' | 'acc_phone';
  provenance?: {
    target_id?: string | null;
    target_kind: string;
    target_channel: 'text' | 'voice';
    target_environment?: string;
    tester_id: 'scenario_simulator' | 'fixture_replay' | 'pipecat_tester';
    executor_id: 'local_async_runner' | 'evidence_replay' | 'cae_local_audio_loop' | 'pipecat_public_daily' | 'signalwire_public_webrtc' | 'acc_browser_webrtc' | 'acc_sip' | 'acc_phone';
    evidence_source: string;
    evidence_capabilities?: string[];
    live_external_connection: boolean;
    saved_evidence: boolean;
    synthetic_media: boolean;
    honesty_label?: string | null;
  } | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

async function createExecutionRun(payload: {
  suite_id: string;
  scenario_ids: string[];
  mode: 'text_callable' | 'voice_fixture' | 'pipecat_webrtc';
  text_callable?: string;
  iterations?: number;
  max_exchanges?: number;
  duplex_timeout_seconds?: number;
  user_id: string;
  project_id: string;
  product_project_id?: string;
  evaluate?: boolean;
  agent_id?: string;
  model_name?: string;
  tester_id?: 'scenario_simulator' | 'fixture_replay' | 'pipecat_tester';
  tester_model_name?: string;
  executor_id?: 'local_async_runner' | 'evidence_replay' | 'cae_local_audio_loop' | 'pipecat_public_daily' | 'signalwire_public_webrtc' | 'acc_browser_webrtc' | 'acc_sip' | 'acc_phone';
  audio_transport?: 'none' | 'pipecat_small_webrtc' | 'pipecat_daily_webrtc' | 'signalwire_webrtc' | 'freeswitch_verto_sip';
}) {
  return handleJson<ExecutionRunRecord>(
    await fetch(`${getApiBase()}/api/execution/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

type ScoreAgentOption = {
  id: string;
  name: string;
  channel: string;
  target: string;
  environment?: 'local' | 'staging' | 'production';
  connection?: {
    endpoint_url?: string | null;
    response_path?: string | null;
    sip_uri?: string | null;
    phone_number?: string | null;
    acc_base_url?: string | null;
  };
  metadata?: {
    model_name?: string | null;
    prompt_version?: string | null;
  };
};

type ReferenceVoicePreflight = {
  ready: boolean;
  llm_mode: 'real' | 'mock';
  dependencies: Array<{ id: string; label: string; ready: boolean; detail: string; setup_url?: string }>;
};

function isFixtureTargetId(target?: string | null) {
  return target === 'mock_agent';
}

function isSavedReplayTargetId(target?: string | null) {
  return target === 'offline_acc_fixture' || target === 'voice_fixture';
}

function isExternalVoiceTargetId(target?: string | null) {
  return target === 'sip_agent' || target === 'phone_agent' || target === 'browser_webrtc_agent';
}

function testerDisplayName(testerId?: string | null) {
  if (testerId === 'fixture_replay') return 'saved conversation replay';
  if (testerId === 'scenario_simulator') return 'scenario simulator';
  if (testerId === 'pipecat_tester') return 'Pipecat voice tester';
  return 'scenario tester';
}

async function listAgents(): Promise<ScoreAgentOption[]> {
  const payload = await handleJson<{ agents?: ScoreAgentOption[] }>(
    await fetch(`${getApiBase()}/api/agents`, { cache: 'no-store' }),
  );
  return payload.agents ?? [];
}

async function fetchReferenceVoicePreflight(): Promise<ReferenceVoicePreflight> {
  const payload = await handleJson<{ reference_voice?: ReferenceVoicePreflight }>(
    await fetch(`${getApiBase()}/api/execution/health`, { cache: 'no-store' }),
  );
  if (!payload.reference_voice) throw new Error('Execution API did not return voice dependency preflight.');
  return payload.reference_voice;
}

function applyAgentProfileDefaults(
  agent: ScoreAgentOption,
  setters: {
    setAgentProfile: (value: string) => void;
    setModelName: (value: string) => void;
    setPromptVersion: (value: string) => void;
  },
) {
  setters.setAgentProfile(agent.name);
  if (agent.metadata?.model_name) setters.setModelName(agent.metadata.model_name);
  if (agent.metadata?.prompt_version) setters.setPromptVersion(agent.metadata.prompt_version);
}

async function fetchExecutionRun(userId: string, executionRunId: string) {
  return handleJson<ExecutionRunRecord>(
    await fetch(
      `${getApiBase()}/api/execution/runs/${encodeURIComponent(executionRunId)}?user_id=${encodeURIComponent(userId)}`,
      { cache: 'no-store' },
    ),
  );
}

async function listExecutionRuns(userId: string, projectId: string) {
  const params = new URLSearchParams({ user_id: userId, project_id: projectId });
  return handleJson<ExecutionRunRecord[]>(
    await fetch(`${getApiBase()}/api/execution/runs?${params.toString()}`, { cache: 'no-store' }),
  );
}

function executionStatusColor(status?: string) {
  if (status === 'completed' || status === 'pass') return 'var(--success-text)';
  if (status === 'failed') return 'var(--error-text)';
  if (status === 'needs_review' || status === 'running' || status === 'queued') return 'var(--warn-text, #9a6700)';
  return 'var(--muted)';
}

function isActiveExecutionStatus(status?: string) {
  return status === 'queued' || status === 'running';
}

function executionRecordingSummary(recording?: JsonRecord | null): string | null {
  if (!recording || typeof recording !== 'object') return null;
  const url = recording.recording_url ?? recording.uri;
  if (typeof url !== 'string' || !url.trim()) return null;
  const mime = typeof recording.mime_type === 'string' ? recording.mime_type : null;
  return mime ? `${url} (${mime})` : url;
}

function executionVconSummary(
  summary?: JsonRecord | null,
  exportPayload?: JsonRecord | null,
): string | null {
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
      : Boolean(
          (exportPayload?.attachments && Array.isArray(exportPayload.attachments) && exportPayload.attachments.length) ||
            exportPayload?.recording_url,
        );
  if (dialogTurns == null && !source && !recordingAttached) return null;
  const parts = [
    source ? `source ${source}` : null,
    dialogTurns != null ? `${dialogTurns} dialog turns` : null,
    recordingAttached ? 'recording attached' : 'no recording',
  ].filter(Boolean);
  return parts.join(' · ');
}

function executionAudioSessionSummary(session?: JsonRecord | null): string | null {
  if (!session || typeof session !== 'object') return null;
  const parts = [
    typeof session.tester_status === 'string' ? `tester ${session.tester_status}` : null,
    typeof session.frames_sent === 'number' ? `sent ${session.frames_sent}` : null,
    typeof session.frames_received === 'number' ? `recv ${session.frames_received}` : null,
    typeof session.bytes_sent === 'number' ? `${session.bytes_sent}B out` : null,
    typeof session.bytes_received === 'number' ? `${session.bytes_received}B in` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : null;
}

async function listSavedRuns(userId: string, projectId: string, suiteId?: string, scenarioId?: string) {
  const params = new URLSearchParams({ user_id: userId, project_id: projectId });
  if (suiteId) params.set('suite_id', suiteId);
  if (scenarioId) params.set('scenario_id', scenarioId);

  return handleJson<SavedRun[]>(
    await fetch(`${getApiBase()}/api/product/runs?${params.toString()}`, { cache: 'no-store' }),
  );
}

async function fetchProjectRegressionSummary(userId: string, projectId: string, suiteId?: string, scenarioId?: string) {
  const params = new URLSearchParams({ user_id: userId });
  if (suiteId) params.set('suite_id', suiteId);
  if (scenarioId) params.set('scenario_id', scenarioId);

  return handleJson<ProjectRegressionSummary>(
    await fetch(`${getApiBase()}/api/product/projects/${encodeURIComponent(projectId)}/regression-summary?${params.toString()}`, { cache: 'no-store' }),
  );
}

async function exportSavedRun(userId: string, runId: string) {
  return handleJson<SavedRunExport>(
    await fetch(`${getApiBase()}/api/product/runs/${encodeURIComponent(runId)}/export?user_id=${encodeURIComponent(userId)}`, { cache: 'no-store' }),
  );
}

async function exportBenchmarkRunAuditArtifacts(userId: string, runId: string) {
  return handleJson<BenchmarkRunAuditArtifactExport>(
    await fetch(`${getApiBase()}/api/benchmarks/runs/${encodeURIComponent(runId)}/audit-artifacts?user_id=${encodeURIComponent(userId)}`, { cache: 'no-store' }),
  );
}

async function exportProjectHistory(userId: string, projectId: string, suiteId?: string, scenarioId?: string) {
  const params = new URLSearchParams({ user_id: userId });
  if (suiteId) params.set('suite_id', suiteId);
  if (scenarioId) params.set('scenario_id', scenarioId);

  return handleJson<ProjectHistoryExport>(
    await fetch(`${getApiBase()}/api/product/projects/${encodeURIComponent(projectId)}/export?${params.toString()}`, { cache: 'no-store' }),
  );
}

async function exportBenchmarkRunHistory(userId: string, projectId: string, suiteId?: string, scenarioId?: string) {
  const params = new URLSearchParams({ user_id: userId, project_id: projectId });
  if (suiteId) params.set('suite_id', suiteId);
  if (scenarioId) params.set('scenario_id', scenarioId);

  return handleJson<BenchmarkRunHistoryExport>(
    await fetch(`${getApiBase()}/api/benchmarks/runs/export?${params.toString()}`, { cache: 'no-store' }),
  );
}

async function exportBenchmarkSuiteRunHistory(userId: string, projectId: string, suiteId?: string, status?: string) {
  const params = new URLSearchParams({ user_id: userId, project_id: projectId });
  if (suiteId) params.set('suite_id', suiteId);
  if (status) params.set('status', status);

  return handleJson<BenchmarkSuiteRunHistoryExport>(
    await fetch(`${getApiBase()}/api/benchmarks/suite-runs/export?${params.toString()}`, { cache: 'no-store' }),
  );
}

async function exportBenchmarkSuiteRunAuditArtifacts(userId: string, suiteRunId: string) {
  return handleJson<BenchmarkSuiteAuditArtifactExport>(
    await fetch(`${getApiBase()}/api/benchmarks/suite-runs/${encodeURIComponent(suiteRunId)}/audit-artifacts?user_id=${encodeURIComponent(userId)}`, { cache: 'no-store' }),
  );
}

async function exportBenchmarkSuiteRunVconBundle(userId: string, suiteRunId: string) {
  return handleJson<BenchmarkSuiteVconBundleExport>(
    await fetch(`${getApiBase()}/api/benchmarks/suite-runs/${encodeURIComponent(suiteRunId)}/vcon-bundle?user_id=${encodeURIComponent(userId)}`, { cache: 'no-store' }),
  );
}

function slugFilenamePart(value: unknown) {
  return String(value).replace(/[^a-z0-9-]+/gi, '-').replace(/^-+|-+$/g, '').toLowerCase();
}

function buildSavedRunAuditArtifactExport(userId: string, run: SavedRun): BenchmarkRunAuditArtifactExport {
  const report = run.report as BenchmarkReport & {
    logical_run_id?: string;
    run_status?: string;
    evidence_artifacts?: JsonRecord;
  };
  const evidenceArtifacts = report.evidence_artifacts && typeof report.evidence_artifacts === 'object' ? report.evidence_artifacts : {};
  const artifacts = Array.isArray(evidenceArtifacts.artifacts) ? evidenceArtifacts.artifacts : [];
  const exportReadiness = report.evidence_audit_summary?.export_readiness;
  const runId = report.run_id ?? run.id;
  const filenameParts = ['agentbench', report.suite_id, report.scenario_id, runId, 'audit-artifacts']
    .filter(Boolean)
    .map(slugFilenamePart);

  return {
    id: runId,
    run_id: runId,
    logical_run_id: report.logical_run_id,
    suite_id: report.suite_id,
    scenario_id: report.scenario_id,
    user_id: userId,
    project_id: run.project_id,
    status: report.run_status ?? report.verdict ?? report.overall,
    filename: `${filenameParts.join('-') || 'agentbench-run-audit-artifacts'}.json`,
    operator_summary: {
      verdict: report.verdict ?? report.overall,
      overall_score: report.overall_score ?? report.score,
      ready_for_export: Boolean(exportReadiness?.ready ?? run.artifacts?.audit_artifacts?.ready_for_export),
      missing_export_artifacts: exportReadiness?.missing ?? run.artifacts?.audit_artifacts?.missing ?? [],
      artifact_count: artifacts.length || run.artifacts?.audit_artifacts?.artifact_types?.length || 0,
      evaluator_version: report.evidence_audit_summary?.evaluator_version ?? run.artifacts?.audit_artifacts?.evaluator_version,
    },
    evidence_fingerprint: evidenceArtifacts.evidence_fingerprint,
    evidence_artifacts: artifacts,
    audit_summary: report.evidence_audit_summary ?? run.artifacts?.audit_artifacts ?? {},
    run_lifecycle: report.run_lifecycle ?? {},
    contract_artifact: {
      type: 'scenario_contract',
      suite_id: report.suite_id,
      scenario_id: report.scenario_id,
      sha256: report.scenario_contract_sha256,
    },
    generated_at: new Date().toISOString(),
  };
}

function downloadJson(filename: string, payload: unknown) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const href = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(href);
}

async function fetchProductConfig(): Promise<ProductConfig> {
  return handleJson<ProductConfig>(await fetch(`${getApiBase()}/api/product/config`, { cache: 'no-store' }));
}

async function fetchOpenAIProviderStatus() {
  return handleJson<OpenAIProviderStatus>(
    await fetch(`${getApiBase()}/api/product/providers/openai/status`, { cache: 'no-store' }),
  );
}

async function startOpenAIProviderOAuth() {
  return handleJson<{ authorize_url: string; redirect_uri: string; provider?: string }>(
    await fetch(`${getApiBase()}/api/product/providers/openai/oauth/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }),
  );
}

async function disconnectOpenAIProvider() {
  return handleJson<OpenAIProviderStatus>(
    await fetch(`${getApiBase()}/api/product/providers/openai/disconnect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }),
  );
}

async function requestJudge(payload: { plan: PricingPlan['id']; report: BenchmarkReport; transcript: string; user_id?: string; project_id?: string }) {
  return handleJson<JudgeGate>(
    await fetch(`${getApiBase()}/api/product/judge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

async function listAuditEvents(userId: string, projectId: string) {
  const params = new URLSearchParams({ user_id: userId, project_id: projectId, limit: '8' });

  return handleJson<ProductAuditEvent[]>(
    await fetch(`${getApiBase()}/api/product/audit-events?${params.toString()}`, { cache: 'no-store' }),
  );
}

function scoreColor(score: number | undefined) {
  if (score === undefined) return 'var(--muted)';
  if (score >= 80) return 'var(--success-text)';
  if (score >= 60) return '#b45309';
  return 'var(--danger)';
}

function formatJudgeSpend(spendControl: JudgeGate['spend_control']) {
  if (!spendControl) return null;

  const estimated = spendControl.estimated_credits ?? 10;
  const remaining = spendControl.remaining_daily_credits;
  const spent = spendControl.spent_daily_credits;
  const limit = spendControl.daily_credit_limit;
  const provider = spendControl.provider ?? 'judge provider';
  const providerStatus = spendControl.provider_configured ? 'configured' : 'not configured';
  const spentLabel = spent === undefined ? '' : ` ${spent} spent;`;

  return `${estimated} credits estimated;${spentLabel} ${remaining ?? 'unknown'} of ${limit ?? 'unknown'} daily credits remaining; ${provider} ${providerStatus}.`;
}

function judgeBannerTitle(judgeGate: JudgeGate) {
  if (judgeGate.status === 'ready') {
    if (judgeGate.judge_result?.agrees === true) return 'LLM judge agrees';
    if (judgeGate.judge_result?.agrees === false) return 'LLM judge disagrees';
    return 'LLM judge complete';
  }
  if (judgeGate.block_reason === 'budget') return 'Judge budget exhausted';
  if (judgeGate.block_reason === 'provider_error') return 'Judge provider error';
  if (judgeGate.block_reason === 'provider') return 'Judge provider required';
  return 'Judge unavailable';
}

function EvidenceItem({ item }: { item: string | JsonRecord }) {
  if (typeof item === "string") {
    return <li>{item}</li>;
  }

  return <li><code>{JSON.stringify(item)}</code></li>;
}

function formatCitationValue(value: unknown) {
  if (value === undefined) {
    return null;
  }

  if (typeof value === "string") {
    return value;
  }

  if (typeof value === "number" || typeof value === "boolean" || value === null) {
    return JSON.stringify(value);
  }

  if (typeof value === "object") {
    return JSON.stringify(value);
  }

  return String(value);
}

function formatCitationItem(item: string | JsonRecord) {
  if (typeof item === "string") {
    return item;
  }

  const sourceKey = typeof item.source === "string" ? item.source : null;
  const source = sourceKey ? sourceKey.replace(/_/g, " ") : "evidence";
  const kind = typeof item.kind === "string" ? item.kind.replace(/_/g, " ") : null;
  const action = typeof item.action === "string" ? item.action : null;
  const reason = typeof item.reason === "string" ? item.reason : null;
  const assertionSummary = formatCitationValue(item.assertion);
  const path = typeof item.path === "string" ? item.path : null;
  const actualSummary = Object.hasOwn(item, "actual") ? formatCitationValue(item.actual) : null;
  const expectedSummary = Object.hasOwn(item, "expected") ? formatCitationValue(item.expected) : null;
  const lineStart = typeof item.line_start === "number" ? item.line_start : null;
  const lineEnd = typeof item.line_end === "number" ? item.line_end : null;
  const textSummary = typeof item.text === "string" ? item.text : null;
  const status = typeof item.status === "string" ? item.status : null;
  const timestamp = typeof item.timestamp === "string" ? item.timestamp : null;
  const lineRange = lineStart === null ? null : lineEnd !== null && lineEnd !== lineStart ? `lines ${lineStart}-${lineEnd}` : `line ${lineStart}`;

  if (kind === "missing action" && action) {
    return [
      sourceKey === "action_trace" ? "action trace" : source,
      `missing required action: ${action}`,
      reason,
    ].filter(Boolean).join(" — ");
  }

  return [
    source,
    kind,
    action,
    assertionSummary,
    sourceKey === "final_state" && path ? `path ${path}` : null,
    sourceKey === "final_state" && actualSummary ? `actual ${actualSummary}` : null,
    sourceKey === "final_state" && expectedSummary ? `expected ${expectedSummary}` : null,
    sourceKey === "transcript" && lineRange ? lineRange : null,
    sourceKey === "transcript" && textSummary ? textSummary : null,
    status ? `status ${status}` : null,
    timestamp ? `at ${timestamp}` : null,
  ].filter(Boolean).join(": ");
}

function formatCitationSummary(items: Array<string | JsonRecord>) {
  return items.length ? items.slice(0, 2).map(formatCitationItem).join("; ") : "None captured";
}

function cleanRunMetadata(metadata: RunMetadata): RunMetadata {
  return Object.fromEntries(
    Object.entries(metadata).map(([key, value]) => [key, value?.trim()]).filter(([, value]) => Boolean(value)),
  ) as RunMetadata;
}

function metadataEntries(metadata?: RunMetadata) {
  const labels: Record<keyof RunMetadata, string> = {
    agent_version: 'Agent target',
    prompt_version: 'Prompt',
    model_name: 'Model',
    notes: 'Notes',
    user_id: 'User',
    project_id: 'Project',
  };

  return (Object.keys(labels) as Array<keyof RunMetadata>)
    .map((key) => ({ key, label: labels[key], value: metadata?.[key] }))
    .filter((item) => item.value);
}

function metadataChangeSummary(current?: RunMetadata, previous?: RunMetadata) {
  const entries = metadataEntries(current);
  const changes = entries
    .filter((item) => previous?.[item.key] !== item.value)
    .map((item) => `${item.label}: ${previous?.[item.key] ?? 'unset'} -> ${item.value}`);

  return changes.length ? changes.join('; ') : entries.length ? 'No version label changes from prior saved run.' : 'No version labels captured.';
}

function regressionDeltaSummary(delta?: RegressionDelta) {
  if (!delta) return 'No prior run comparison captured.';
  if (delta.status === 'baseline') return 'Baseline run for this project.';
  const currentScore = delta.current_overall_score ?? 'n/a';
  const previousScore = delta.previous_overall_score ?? 'n/a';
  const signedDelta = typeof delta.score_delta === 'number' && delta.score_delta > 0 ? `+${delta.score_delta}` : delta.score_delta ?? 'n/a';

  return `${delta.status ?? 'compared'}: ${currentScore} vs ${previousScore} (${signedDelta})`;
}

function regressionDeltaColor(status?: string) {
  if (status === 'improved') return 'var(--success-text)';
  if (status === 'regressed') return 'var(--danger)';
  if (status === 'unchanged') return 'var(--muted)';
  return 'var(--text)';
}

function reportScore(report?: BenchmarkReport | null) {
  const score = report?.overall_score ?? report?.score;
  return typeof score === 'number' && !Number.isNaN(score) ? score : null;
}

function currentReportRegressionDelta(report: BenchmarkReport | null, savedRuns: SavedRun[]): RegressionDelta | null {
  const currentScore = reportScore(report);
  if (!report || currentScore === null) return null;

  const priorRun = [...savedRuns]
    .sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())
    .find((run) => {
      if (report.scenario_id && run.report.scenario_id !== report.scenario_id) return false;
      if (report.suite_id && run.report.suite_id !== report.suite_id) return false;
      return run.report.run_id !== report.run_id;
    });
  const previousScore = reportScore(priorRun?.report);

  if (!priorRun || previousScore === null) {
    return {
      status: 'baseline',
      previous_run_id: null,
      previous_overall_score: null,
      current_overall_score: currentScore,
      score_delta: null,
    };
  }

  const scoreDelta = currentScore - previousScore;
  return {
    status: scoreDelta > 0 ? 'improved' : scoreDelta < 0 ? 'regressed' : 'unchanged',
    previous_run_id: priorRun.report.run_id ?? priorRun.id,
    previous_overall_score: previousScore,
    current_overall_score: currentScore,
    score_delta: scoreDelta,
  };
}

function savedRunVconSummary(summary?: SavedRunVconExportSummary) {
  if (!summary?.available) return 'vCon export not captured.';
  const source = summary.source_format ?? 'benchmark';
  const analysis = summary.appended_analysis_type ?? 'agentic_benchmark_eval';
  return `vCon ready: ${summary.dialog_turns ?? 0} dialog turns, ${summary.analysis_count ?? 0} analysis records (${source}, ${analysis}).`;
}

function savedRunAuditArtifactSummary(summary?: SavedRunAuditArtifactSummary) {
  if (!summary?.available) return 'Audit artifacts not captured.';
  const artifactTypes = summary.artifact_types?.length ? summary.artifact_types.map(artifactLabel).join(', ') : 'none';
  if (summary.ready_for_export) {
    const evaluatorLabel = summary.classification === 'unsupported' ? 'unsupported evaluator' : summary.evaluator_version ?? 'ASSERT';
    return `Audit export ready: ${artifactTypes} (${evaluatorLabel}).`;
  }
  const missing = summary.missing?.length ? summary.missing.map(artifactLabel).join(', ') : 'not specified';
  return `Audit export incomplete: missing ${missing}.`;
}

function savedRunContractArtifactSummary(summary?: SavedRunContractArtifactSummary) {
  if (!summary?.available) return 'Contract artifacts not captured.';
  const suiteHash = summary.suite_contract_manifest_sha256 ? summary.suite_contract_manifest_sha256.slice(0, 12) : 'n/a';
  const scenarioHash = summary.scenario_contract_sha256 ? summary.scenario_contract_sha256.slice(0, 12) : 'n/a';
  return `Contract artifacts ready: suite ${suiteHash}, scenario ${scenarioHash}.`;
}

function projectVconExportSummary(summary?: ProjectVconExportSummary) {
  if (!summary?.available_records) return 'No vCon-ready saved runs captured.';
  return `${summary.available_records}/${summary.total_runs} vCon-ready runs with ${summary.dialog_turns} dialog turns and ${summary.analysis_records} analysis records.`;
}

function projectContractArtifactSummary(summary?: ProjectContractArtifactSummary) {
  if (!summary?.available_records) return 'No contract fingerprints captured.';
  const suiteCount = summary.suite_contract_manifest_sha256s?.length ?? 0;
  const scenarioCount = summary.scenario_contract_sha256s?.length ?? 0;
  return `${summary.available_records}/${summary.total_runs} runs include contract fingerprints (${suiteCount} suite, ${scenarioCount} scenario).`;
}

function scenarioCoverageExportSummary(summary?: ScenarioCoverageSummary) {
  if (!summary) return 'Scenario coverage unavailable.';
  const coveredScenarios = summary.covered_scenarios?.length
    ? summary.covered_scenarios.map((scenario) => scenario.title ?? scenario.id).filter(Boolean)
    : summary.covered_scenario_ids ?? [];
  const outOfSuiteScenarios = summary.out_of_suite_scenarios?.length
    ? summary.out_of_suite_scenarios.map((scenario) => scenario.title ?? scenario.id).filter(Boolean)
    : summary.out_of_suite_scenario_ids ?? [];
  const outOfSuiteCount = outOfSuiteScenarios.length;
  const outOfSuitePreview = outOfSuiteScenarios.slice(0, 2).join(', ');
  const outOfSuiteSummary = outOfSuiteCount ? ` Outside suite: ${outOfSuitePreview}${outOfSuiteCount > 2 ? `, +${outOfSuiteCount - 2} more` : ''}.` : '';
  if (typeof summary.scenario_count !== 'number') {
    const coveredPreview = coveredScenarios.slice(0, 2).join(', ');
    return `${summary.covered_scenario_count ?? 0} distinct scenarios covered${coveredPreview ? `: ${coveredPreview}` : ''}.${outOfSuiteSummary}`;
  }
  if (summary.scenario_count === 0) {
    return `No suite scenarios configured.${outOfSuiteSummary}`;
  }

  const coverage = typeof summary.coverage_percent === 'number' ? `${summary.coverage_percent}%` : 'n/a';
  const missingScenarios = summary.missing_scenarios?.length
    ? summary.missing_scenarios.map((scenario) => scenario.title ?? scenario.id).filter(Boolean)
    : summary.missing_scenario_ids ?? [];
  const missingCount = missingScenarios.length;
  const missingPreview = missingScenarios.slice(0, 2).join(', ');
  const missingOverflow = missingCount > 2 ? `, +${missingCount - 2} more` : '';
  const nextScenario = summary.recommended_next_scenario?.title ?? summary.recommended_next_scenario?.id;
  const nextStep = nextScenario
    ? summary.coverage_status === 'empty'
      ? ` Start with ${nextScenario}.`
      : ` Next: ${nextScenario}.`
    : '';
  const coveredPreview = !missingCount && coveredScenarios.length ? ` Covered: ${coveredScenarios.slice(0, 2).join(', ')}.` : '';
  if (summary.coverage_status === 'complete' || (!missingCount && summary.covered_scenario_count === summary.scenario_count)) {
    return `${summary.covered_scenario_count ?? 0}/${summary.scenario_count} suite scenarios covered (${coverage}); full suite covered.${coveredPreview}${outOfSuiteSummary}`;
  }
  return `${summary.covered_scenario_count ?? 0}/${summary.scenario_count} suite scenarios covered (${coverage}); ${missingCount} missing${missingPreview ? `: ${missingPreview}${missingOverflow}` : ''}.${nextStep}${coveredPreview}${outOfSuiteSummary}`;
}


function scenarioCoverageFromRuns(
  suite: BenchmarkSuite | null,
  runs: SavedRun[],
  currentReport?: BenchmarkReport | null,
): ScenarioCoverageSummary | null {
  if (!suite) return null;
  const scenarioTitles = new Map(suite.scenarios.map((scenario) => [scenario.id, scenario.title]));
  const outOfSuiteScenarios = new Map<string, string>();
  const coveredIds = new Set(
    runs
      .map((run) => {
        const scenarioId = run.report.scenario_id;
        if (!scenarioId) return null;
        if (!scenarioTitles.has(scenarioId)) {
          outOfSuiteScenarios.set(scenarioId, run.report.scenario_title ?? scenarioId);
          return null;
        }
        return scenarioId;
      })
      .filter((scenarioId): scenarioId is string => Boolean(scenarioId)),
  );
  if (currentReport?.scenario_id) {
    if (scenarioTitles.has(currentReport.scenario_id)) {
      coveredIds.add(currentReport.scenario_id);
    } else if ((currentReport.suite_id ?? suite.id) === suite.id) {
      outOfSuiteScenarios.set(currentReport.scenario_id, currentReport.scenario_title ?? currentReport.scenario_id);
    }
  }
  const coveredScenarioIds = suite.scenarios.map((scenario) => scenario.id).filter((scenarioId) => coveredIds.has(scenarioId));
  const missingScenarioIds = suite.scenarios.map((scenario) => scenario.id).filter((scenarioId) => !coveredIds.has(scenarioId));
  const recommendedNextScenario = missingScenarioIds[0] ?? null;
  const outOfSuiteScenarioIds = [...outOfSuiteScenarios.keys()];

  return {
    suite_id: suite.id,
    scenario_count: suite.scenarios.length,
    covered_scenario_count: coveredScenarioIds.length,
    coverage_percent: suite.scenarios.length ? Math.round((coveredScenarioIds.length / suite.scenarios.length) * 10000) / 100 : null,
    covered_scenario_ids: coveredScenarioIds,
    missing_scenario_ids: missingScenarioIds,
    out_of_suite_scenario_ids: outOfSuiteScenarioIds,
    covered_scenarios: coveredScenarioIds.map((scenarioId) => ({ id: scenarioId, title: scenarioTitles.get(scenarioId) ?? scenarioId })),
    missing_scenarios: missingScenarioIds.map((scenarioId) => ({ id: scenarioId, title: scenarioTitles.get(scenarioId) ?? scenarioId })),
    out_of_suite_scenarios: outOfSuiteScenarioIds.map((scenarioId) => ({
      id: scenarioId,
      title: outOfSuiteScenarios.get(scenarioId) ?? scenarioId,
    })),
    recommended_next_scenario: recommendedNextScenario
      ? { id: recommendedNextScenario, title: scenarioTitles.get(recommendedNextScenario) ?? recommendedNextScenario }
      : null,
    coverage_status: missingScenarioIds.length === 0 && suite.scenarios.length ? 'complete' : coveredScenarioIds.length ? 'partial' : 'empty',
  };
}

function benchmarkRunHistoryExportSummary(summary?: BenchmarkRunHistoryExport['summary']) {
  if (!summary) return 'Benchmark trend not available.';
  const trend = summary.latest_trend ? summary.latest_trend.replace(/_/g, ' ') : 'unavailable';
  const top = summary.top_failure_categories?.[0];
  const topIssue = top?.category && top.count > 0
    ? `Top issue: ${top.category.replace(/_/g, ' ')} (${top.count}).`
    : 'No recurring failure category.';
  return `Benchmark trend ${trend}: ${summary.latest_score ?? 'n/a'} vs ${summary.previous_score ?? 'n/a'} (${formatSignedDelta(summary.latest_delta)}). ${topIssue}`;
}

function suiteHistoryExportSummary(summary?: BenchmarkSuiteRunHistoryExport['summary']) {
  if (!summary) return 'Suite trend not available.';
  const trend = summary.latest_trend ? summary.latest_trend.replace(/_/g, ' ') : 'unavailable';
  const passRate = typeof summary.pass_rate === 'number' ? `${summary.pass_rate}% pass rate` : 'pass rate unavailable';
  const topFailure = suiteHistoryFailureSummary(summary);
  return `Suite trend ${trend}: ${summary.latest_average_score ?? 'n/a'} vs ${summary.previous_average_score ?? 'n/a'} (${formatSignedDelta(summary.latest_delta)}), ${passRate}. ${topFailure}`;
}

function suiteHistoryFailureSummary(summary?: BenchmarkSuiteRunHistoryExport['summary']) {
  const top = summary?.top_failure_categories?.[0];
  if (top?.category && top.count > 0) {
    return `Top issue: ${top.category.replace(/_/g, ' ')} (${top.count}).`;
  }
  return 'No recurring failure category.';
}

function scenarioFailureCategorySummary(categories?: string[]) {
  const cleaned = (categories ?? []).filter(Boolean).map((category) => category.replace(/_/g, ' '));
  return cleaned.length ? ` - ${cleaned.join(', ')}` : '';
}

function suiteRunFailureCategorySummary(scenarios: BenchmarkSuiteScenarioSummary[]) {
  const counts = scenarios.reduce<Record<string, number>>((accumulator, scenario) => {
    for (const category of scenario.failure_categories ?? []) {
      if (!category) continue;
      accumulator[category] = (accumulator[category] ?? 0) + 1;
    }
    return accumulator;
  }, {});
  const topCategories = topFailureCategories(counts).map(({ category, count }) => `${category.replace(/_/g, ' ')} (${count})`);
  return topCategories.length ? `Failure mix: ${topCategories.join(', ')}.` : '';
}

function suiteHistorySummaryFromRuns(runs: BenchmarkSuiteRunRecord[]): BenchmarkSuiteRunHistoryExport['summary'] | null {
  if (!runs.length) return null;
  const latest = runs[0];
  const previous = runs.slice(1).find((run) => typeof run.average_score === 'number');
  const latestScore = typeof latest.average_score === 'number' ? latest.average_score : null;
  const previousScore = typeof previous?.average_score === 'number' ? previous.average_score : null;
  const totalScenarios = runs.reduce((total, run) => total + Math.max(run.scenario_count ?? 0, 0), 0);
  const totalPasses = runs.reduce((total, run) => total + Math.max(run.pass_count ?? 0, 0), 0);
  const failureCategoryCounts = suiteFailureCategoryCounts(runs);
  return {
    latest_suite_run_id: latest.suite_run_id,
    latest_status: latest.status,
    latest_average_score: latestScore,
    previous_average_score: previousScore,
    latest_delta: latestScore !== null && previousScore !== null ? latestScore - previousScore : null,
    latest_trend: scoreTrend(latestScore, previousScore),
    total_scenarios: totalScenarios,
    total_passes: totalPasses,
    total_needs_review: runs.reduce((total, run) => total + Math.max(run.needs_review_count ?? 0, 0), 0),
    pass_rate: totalScenarios ? Math.round((totalPasses / totalScenarios) * 10000) / 100 : null,
    failure_category_counts: failureCategoryCounts,
    top_failure_categories: topFailureCategories(failureCategoryCounts),
  };
}

function suiteFailureCategoryCounts(runs: BenchmarkSuiteRunRecord[]) {
  return runs.reduce<Record<string, number>>((counts, run) => {
    for (const scenario of run.artifacts?.scenario_summaries ?? []) {
      for (const category of scenario.failure_categories ?? []) {
        if (!category) continue;
        counts[category] = (counts[category] ?? 0) + 1;
      }
    }
    return counts;
  }, {});
}

function topFailureCategories(counts: Record<string, number>) {
  return Object.entries(counts)
    .sort(([leftCategory, leftCount], [rightCategory, rightCount]) => rightCount - leftCount || leftCategory.localeCompare(rightCategory))
    .slice(0, 5)
    .map(([category, count]) => ({ category, count }));
}

function scoreTrend(latestScore: number | null, previousScore: number | null) {
  if (latestScore === null) return 'unscored';
  if (previousScore === null) return 'baseline';
  if (latestScore > previousScore) return 'improved';
  if (latestScore < previousScore) return 'regressed';
  return 'unchanged';
}

function formatSignedDelta(value?: number | null) {
  if (typeof value !== 'number') return 'n/a';
  return value > 0 ? `+${value}` : String(value);
}

function scenarioSummaryLabel(summary: ScenarioRegressionSummary) {
  return summary.suite_id ? `${summary.suite_id} / ${summary.scenario_id}` : summary.scenario_id;
}

function suiteRunStatusColor(status?: string) {
  if (status === 'completed') return 'var(--success-text)';
  if (status === 'failed') return 'var(--danger)';
  if (status === 'needs_review') return '#b45309';
  if (status === 'queued' || status === 'running') return 'var(--accent)';
  return 'var(--muted)';
}

function isActiveSuiteRunStatus(status?: string) {
  return status === 'queued' || status === 'running';
}

function formatHistoryDate(value?: string | null) {
  if (!value) return 'n/a';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function latestSuiteRunUpdatedAt(runs: BenchmarkSuiteRunRecord[]) {
  let latestTime = 0;
  let latestValue: string | null = null;
  for (const run of runs) {
    if (!run.updated_at) continue;
    const parsedTime = new Date(run.updated_at).getTime();
    if (Number.isNaN(parsedTime)) continue;
    if (parsedTime > latestTime) {
      latestTime = parsedTime;
      latestValue = run.updated_at;
    }
  }
  return latestValue;
}

function auditEventSummary(event: ProductAuditEvent) {
  const payload = event.payload ?? {};
  if (event.event_type === 'run.saved') {
    return `Saved ${String(payload.scenario_id ?? 'benchmark run')} at ${String(payload.overall_score ?? 'n/a')}.`;
  }
  if (event.event_type === 'run.exported') {
    return `Exported ${String(payload.export_type ?? 'run')} ${String(payload.run_id ?? '')}`.trim();
  }
  if (event.event_type === 'judge.requested') {
    return `Judge ${String(payload.status ?? 'requested')} for ${String(payload.credits ?? 'n/a')} credits.`;
  }
  return event.event_type.replace(/\./g, ' ');
}

function suiteRunTitle(run: BenchmarkSuiteRunRecord) {
  return run.suite_report?.suite_name ?? run.suite_id;
}

function suiteRunVconSummary(run: BenchmarkSuiteRunRecord) {
  const summary = run.artifacts?.vcon_export;
  if (!summary?.available) return 'vCon bundle not captured yet.';
  return `${summary.dialog_turns ?? 0} dialog turns, ${summary.analysis_count ?? 0} analysis records (${summary.source_format ?? 'suite'}).`;
}

function suiteRunLifecycleSummary(lifecycle?: BenchmarkSuiteRunRecord['run_lifecycle']) {
  if (!lifecycle?.transitions?.length) return null;

  const path = lifecycle.transitions
    .map((transition) => transition.to ?? 'unknown')
    .filter(Boolean)
    .join(' -> ');

  if (!path) return null;

  return `Lifecycle: ${path}`;
}

function suiteRunLifecycleTimeline(lifecycle?: BenchmarkSuiteRunRecord['run_lifecycle']) {
  return lifecycle?.transitions?.filter((transition) => transition.to || transition.reason || transition.at) ?? [];
}

function suiteReliabilityMetrics(run: BenchmarkSuiteRunRecord) {
  return run.reliability_metrics ?? run.suite_report?.reliability_metrics ?? {};
}

function formatSuiteRunProgress(progress?: BenchmarkSuiteRunProgress) {
  if (!progress) return 'n/a';
  const completed = progress.completed_scenarios ?? 0;
  const total = progress.total_scenarios ?? 0;
  const percent = typeof progress.percent === 'number' ? `${progress.percent}%` : 'n/a';
  return `${completed}/${total} (${percent})`;
}

function mergeSuiteRunRecords(
  currentRuns: BenchmarkSuiteRunRecord[],
  incomingRuns: BenchmarkSuiteRunRecord[],
  suiteId?: string,
  statusFilter?: string,
) {
  const mergedRuns = [...incomingRuns];
  const seenSuiteRunIds = new Set(incomingRuns.map((run) => run.suite_run_id));

  currentRuns.forEach((run) => {
    if (seenSuiteRunIds.has(run.suite_run_id)) return;
    if (!isActiveSuiteRunStatus(run.status)) return;
    if (suiteId && run.suite_id !== suiteId) return;
    if (statusFilter && run.status !== statusFilter) return;

    mergedRuns.push(run);
  });

  return mergedRuns;
}

function formatMetricPercent(value?: number) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 'n/a';
  return `${Math.round(value * 100)}%`;
}

function formatAuditTimestamp(value?: string) {
  if (!value) return 'Not captured';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatBoolean(value?: boolean) {
  return value ? 'Present' : 'Missing';
}

function artifactLabel(value: string) {
  return value.replace(/_/g, ' ');
}

function formatForbiddenActionHit(hit: string | JsonRecord) {
  if (typeof hit === 'string') return hit;

  const action = typeof hit.action === 'string' ? hit.action : null;
  const evidence = typeof hit.evidence === 'string' ? hit.evidence : null;
  if (action && evidence) return `${action} (${evidence})`;
  if (action) return action;

  return JSON.stringify(hit);
}

function formatReportBrief(
  report: BenchmarkReport,
  fallbackScenarioTitle?: string,
  regressionDelta?: RegressionDelta | null,
  evidenceCitations?: Array<string | JsonRecord>,
  suiteCoverage?: ScenarioCoverageSummary | null,
  actionPlan?: { primaryRisk: string; nextStep: string } | null,
) {
  const verdict = report.verdict ?? report.overall ?? 'complete';
  const score = report.score ?? report.overall_score ?? 'n/a';
  const scenario = report.scenario_title ?? fallbackScenarioTitle ?? 'Selected scenario';
  const suiteFingerprint = report.suite_contract_manifest_sha256 ? report.suite_contract_manifest_sha256.slice(0, 12) : 'Not captured';
  const contractFingerprint = report.scenario_contract_sha256 ? report.scenario_contract_sha256.slice(0, 12) : 'Not captured';
  const failureCategories = report.failure_categories?.length ? report.failure_categories.join(', ') : 'None reported';
  const missingActions = report.missing_actions?.length ? report.missing_actions.join('; ') : 'None reported';
  const forbiddenActions = report.forbidden_actions_observed?.length
    ? report.forbidden_actions_observed.join('; ')
    : report.forbidden_action_hits?.length
      ? report.forbidden_action_hits.map(formatForbiddenActionHit).join('; ')
      : 'None reported';
  const suggestedFixes = report.suggested_fixes?.length
    ? report.suggested_fixes.join('; ')
    : report.recommendations?.length
      ? report.recommendations.join('; ')
      : 'None reported';

  return [
    `Scenario: ${scenario}`,
    `Verdict: ${verdict}`,
    `Score: ${score}`,
    `Suite contract manifest: ${suiteFingerprint}`,
    `Scenario contract: ${contractFingerprint}`,
    `Failure categories: ${failureCategories}`,
    `Regression: ${regressionDelta ? regressionDeltaSummary(regressionDelta) : 'Not compared'}`,
    `Suite coverage: ${suiteCoverage ? scenarioCoverageExportSummary(suiteCoverage) : 'Not available'}`,
    `Primary risk: ${actionPlan?.primaryRisk ?? 'Not available'}`,
    `Next step: ${actionPlan?.nextStep ?? 'Not available'}`,
    `Missing actions: ${missingActions}`,
    `Evidence citations: ${formatCitationSummary(evidenceCitations ?? report.evidence_citations ?? [])}`,
    `Forbidden actions observed: ${forbiddenActions}`,
    `Suggested fixes: ${suggestedFixes}`,
  ].join('\n');
}

function reportActionPlan(
  report: BenchmarkReport,
  regressionDelta?: RegressionDelta | null,
  suiteCoverage?: ScenarioCoverageSummary | null,
) {
  const verdict = (report.verdict ?? report.overall ?? '').toLowerCase();
  const score = report.score ?? report.overall_score;
  const validationMissingActions = report.simulation_validation?.missing_required_actions ?? [];
  const missingActionHints = [
    ...(report.missing_actions ?? []),
    ...validationMissingActions.map((action) => `Add explicit tool/action execution for: ${action}`),
  ];
  const missingCount = missingActionHints.length;
  const forbiddenCount = (report.forbidden_actions_observed?.length ?? report.forbidden_action_hits?.length) ?? 0;
  const failureCategory = report.failure_categories?.[0]?.replace(/_/g, ' ') ?? null;
  const failureCategories = report.failure_categories?.length
    ? report.failure_categories.map((category) => category.replace(/_/g, ' ')).join(', ')
    : null;
  const suggestedFix = report.suggested_fixes?.[0] ?? report.recommendations?.[0] ?? null;
  const isPass = verdict === 'pass' || (typeof score === 'number' && score >= 80 && missingCount === 0 && forbiddenCount === 0);
  const uncoveredCount = suiteCoverage?.missing_scenarios?.length ?? suiteCoverage?.missing_scenario_ids?.length ?? 0;
  const nextCoverageScenario = suiteCoverage?.recommended_next_scenario?.title ?? suiteCoverage?.recommended_next_scenario?.id ?? null;
  const hasCoverageGap = isPass && suiteCoverage?.coverage_status !== 'complete' && uncoveredCount > 0;

  const headline = hasCoverageGap
    ? 'Keep moving through uncovered scenarios'
    : isPass
      ? 'Ready for release review'
      : 'Needs operator review';
  const primaryRisk = isPass
    ? hasCoverageGap
      ? `${uncoveredCount} suite scenario${uncoveredCount === 1 ? '' : 's'} still need fresh coverage before release review.`
      : 'No blocking failure category was reported for this scenario.'
    : failureCategory ?? (missingCount ? `${missingCount} required action${missingCount === 1 ? '' : 's'} missing` : 'Benchmark evidence needs review');
  const nextStep = isPass
    ? hasCoverageGap && nextCoverageScenario
      ? `Run ${nextCoverageScenario} next to keep suite coverage moving before release review.`
      : 'Save this run as the baseline, then compare the next prompt or model change against it.'
    : missingCount > 1
      ? `Complete the remaining ${missingCount} required actions (next: ${report.missing_actions?.[0] ?? 'see checklist'}).`
      : suggestedFix ?? 'Fix the highest-risk failure, regenerate evidence, and rerun this scenario before release.';
  const regression = regressionDelta?.status === 'baseline'
    ? 'Not compared yet — save this run to start regression tracking.'
    : regressionDelta
      ? regressionDeltaSummary(regressionDelta)
      : 'Save the run to establish regression tracking.';

  return { headline, primaryRisk, nextStep, regression, failureCategories };
}

function formatSuiteBrief(simulation: BenchmarkSuiteSimulationResponse) {
  const needsReview = simulation.scenario_runs
    .filter((run) => run.benchmark_report.verdict !== 'pass')
    .map((run) => run.scenario_title ?? run.benchmark_report.scenario_title ?? run.scenario_id ?? run.benchmark_report.scenario_id ?? 'Unnamed scenario');

  return [
    `Suite: ${simulation.suite_name ?? simulation.suite_id}`,
    `Verdict: ${simulation.verdict}`,
    `Average score: ${simulation.average_score}`,
    `Scenarios: ${simulation.scenario_count}`,
    `Passing: ${simulation.pass_count}`,
    `Needs review: ${simulation.needs_review_count}`,
    `Review scenarios: ${needsReview.length ? needsReview.join('; ') : 'None reported'}`,
    `Suite run: ${simulation.suite_run_id}`,
  ].join('\n');
}

function onboardingStatusLabel(done: boolean, ready: boolean) {
  if (done) return 'Done';
  if (ready) return 'Ready';
  return 'Next';
}

function starterDataSummary(scenario?: BenchmarkScenario | null) {
  if (!scenario) return 'Choose a sample scenario to preload starter evidence.';

  const available = [
    scenario.sample_transcript ? 'transcript' : null,
    scenario.sample_action_trace ? 'action trace' : null,
    scenario.sample_final_state ?? scenario.expected_final_state ? 'final state' : null,
  ].filter(Boolean) as string[];

  if (!available.length) {
    return 'No starter evidence is attached to this scenario yet.';
  }

  return `Starter evidence ready: ${available.join(', ')}.`;
}

async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Fall back to the textarea copy path below when async clipboard is blocked.
    }
  }

  if (typeof document.execCommand !== 'function') {
    throw new Error('Clipboard copy is not supported.');
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'true');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();

  try {
    const copied = document.execCommand('copy');
    if (!copied) {
      throw new Error('Clipboard copy failed.');
    }
  } finally {
    textarea.remove();
  }
}

export type BenchmarkRunnerView = 'all' | 'simulate' | 'score' | 'run';

const WORKFLOW_DEMO_PRESETS: Record<string, { suiteId: string; scenarioId: string }> = {
  'angry-caller': { suiteId: 'call-center-voice-ai', scenarioId: 'angry-outage-escalation' },
  'sample-evidence': { suiteId: 'call-center-voice-ai', scenarioId: 'billing-address-change' },
};

function readWorkflowDemoPreset() {
  if (typeof window === 'undefined') return null;
  const params = new URLSearchParams(window.location.search);
  const suiteId = params.get('suite_id');
  const scenarioId = params.get('scenario_id');
  if (suiteId && scenarioId) return { suiteId, scenarioId };
  const demo = params.get('demo');
  if (!demo) return null;
  return WORKFLOW_DEMO_PRESETS[demo] ?? null;
}

function shouldPreloadSampleEvidence() {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  return params.get('demo') === 'sample-evidence' || params.get('sample') === '1';
}

function transcriptFromVcon(vcon: JsonRecord): string {
  const parties = Array.isArray(vcon.parties) ? vcon.parties : [];
  const dialog = Array.isArray(vcon.dialog) ? vcon.dialog : [];
  return dialog
    .map((item) => {
      const record = asRecord(item);
      const partyIndex = Number(record.party ?? 0);
      const party = asRecord(parties[partyIndex]);
      const name = String(party.name ?? party.role ?? `party-${partyIndex}`);
      const body = String(record.body ?? record.text ?? '').trim();
      return body ? `${name}: ${body}` : '';
    })
    .filter(Boolean)
    .join('\n');
}

function sampleVconFromTranscript(transcriptText: string): string {
  const lines = transcriptText
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const parties = [{ name: 'Caller' }, { name: 'Agent' }];
  const dialog = lines.map((line) => {
    const matched = line.match(/^(caller|agent|user|customer)\s*:\s*(.*)$/i);
    if (matched) {
      const speaker = matched[1].toLowerCase();
      const party = speaker === 'agent' ? 1 : 0;
      return { party, body: matched[2] };
    }
    return { party: 0, body: line };
  });
  return JSON.stringify({ vcon: '0.0.1', parties, dialog }, null, 2);
}

function describeUploadedEvidence(filename: string, text: string): {
  kind: 'vcon' | 'transcript';
  transcript?: string;
  vcon?: string;
  message: string;
} {
  const trimmed = text.trim();
  const lower = filename.toLowerCase();
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const record = asRecord(parsed);
      const looksVcon =
        lower.endsWith('.vcon')
        || typeof record.vcon === 'string'
        || (Array.isArray(record.parties) && Array.isArray(record.dialog));
      if (looksVcon) {
        const derived = transcriptFromVcon(record);
        return {
          kind: 'vcon',
          vcon: JSON.stringify(parsed, null, 2),
          transcript: derived || undefined,
          message: `Loaded vCon from ${filename}.`,
        };
      }
      if (typeof record.transcript === 'string') {
        return {
          kind: 'transcript',
          transcript: record.transcript,
          message: `Loaded transcript from ${filename}.`,
        };
      }
    }
  } catch {
    // Plain text transcript.
  }
  return {
    kind: 'transcript',
    transcript: trimmed,
    message: `Loaded transcript from ${filename}.`,
  };
}

export function BenchmarkRunner({
  view = 'all',
  onExecutionCreated,
  onExecutionUpdated,
}: {
  view?: BenchmarkRunnerView;
  onExecutionCreated?: (run: ExecutionRunRecord) => void;
  onExecutionUpdated?: (run: ExecutionRunRecord) => void;
}) {
  const loadingSavedRunRef = useRef(false);
  const autoLaunchDemoRef = useRef(false);
  const preserveScoreEvidenceRef = useRef(false);
  const [suites, setSuites] = useState<BenchmarkSuite[]>([]);
  const [selectedSuiteId, setSelectedSuiteId] = useState('');
  const [selectedScenarioId, setSelectedScenarioId] = useState('');
  const [transcript, setTranscript] = useState('');
  const [actionTrace, setActionTrace] = useState('');
  const [finalState, setFinalState] = useState('');
  const [callEvidence, setCallEvidence] = useState('');
  const [groupCall, setGroupCall] = useState('');
  const [vconEvidence, setVconEvidence] = useState('');
  const [agentProfile, setAgentProfile] = useState('');
  const [agentVersion, setAgentVersion] = useState('');
  const [promptVersion, setPromptVersion] = useState('');
  const [modelName, setModelName] = useState('');
  const [runNotes, setRunNotes] = useState('');
  const [includeFailure, setIncludeFailure] = useState(false);
  const [report, setReport] = useState<BenchmarkReport | null>(null);
  const [suiteSimulation, setSuiteSimulation] = useState<BenchmarkSuiteSimulationResponse | null>(null);
  const [contractManifest, setContractManifest] = useState<BenchmarkSuiteContractManifest | null>(null);
  const [contractManifestError, setContractManifestError] = useState<string | null>(null);
  const [userId, setUserId] = useState('');
  const [projectId, setProjectId] = useState('call-center-demo');
  const [productProjects, setProductProjects] = useState<ProductProjectOption[]>([]);
  const [productProjectId, setProductProjectId] = useState('');
  const [plan, setPlan] = useState<PricingPlan['id']>('free');
  const [productConfig, setProductConfig] = useState<ProductConfig | null>(null);
  const [savedRuns, setSavedRuns] = useState<SavedRun[]>([]);
  const [suiteSavedRuns, setSuiteSavedRuns] = useState<SavedRun[]>([]);
  const [auditEvents, setAuditEvents] = useState<ProductAuditEvent[]>([]);
  const [suiteRuns, setSuiteRuns] = useState<BenchmarkSuiteRunRecord[]>([]);
  const visibleSuiteHistorySummary = useMemo(() => suiteHistorySummaryFromRuns(suiteRuns), [suiteRuns]);
  const latestSuiteRunUpdate = useMemo(() => latestSuiteRunUpdatedAt(suiteRuns), [suiteRuns]);
  const [suiteRunStatusFilter, setSuiteRunStatusFilter] = useState('');
  const [isRefreshingSuiteRuns, setIsRefreshingSuiteRuns] = useState(false);
  const [projectRegressionSummary, setProjectRegressionSummary] = useState<ProjectRegressionSummary | null>(null);
  const [scenarioRegressionSummary, setScenarioRegressionSummary] = useState<ProjectRegressionSummary | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [exportMessage, setExportMessage] = useState<string | null>(null);
  const [judgeGate, setJudgeGate] = useState<JudgeGate | null>(null);
  const [isJudging, setIsJudging] = useState(false);
  const [showJudgePrompt, setShowJudgePrompt] = useState(false);
  const [openaiProvider, setOpenaiProvider] = useState<OpenAIProviderStatus | null>(null);
  const [openaiProviderMessage, setOpenaiProviderMessage] = useState<string | null>(null);
  const [isConnectingOpenAI, setIsConnectingOpenAI] = useState(false);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [isSimulating, setIsSimulating] = useState(false);
  const [isEnqueueingSuite, setIsEnqueueingSuite] = useState(false);
  const [executionMode, setExecutionMode] = useState<'text_callable' | 'voice_fixture' | 'pipecat_webrtc'>('text_callable');
  const [executionScope, setExecutionScope] = useState<'selected' | 'suite'>('selected');
  const [executionIterations, setExecutionIterations] = useState(1);
  const [executionMaxExchanges, setExecutionMaxExchanges] = useState<number | ''>(3);
  const [executionDuplexTimeoutSeconds, setExecutionDuplexTimeoutSeconds] = useState(120);
  const [executionTesterId, setExecutionTesterId] = useState<'scenario_simulator' | 'fixture_replay' | 'pipecat_tester'>('scenario_simulator');
  const [executionExecutorId, setExecutionExecutorId] = useState<NonNullable<ExecutionRunRecord['executor_id']>>('local_async_runner');
  const [executionRun, setExecutionRun] = useState<ExecutionRunRecord | null>(null);
  const [isLaunchingExecution, setIsLaunchingExecution] = useState(false);
  const [executionMessage, setExecutionMessage] = useState<string | null>(null);
  const [executionModelName, setExecutionModelName] = useState(DEFAULT_EXECUTION_MODEL);
  const [executionModelOptions, setExecutionModelOptions] = useState<string[]>([DEFAULT_EXECUTION_MODEL]);
  const [executionModelsMessage, setExecutionModelsMessage] = useState<string | null>(null);
  const [agents, setAgents] = useState<ScoreAgentOption[]>([]);
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [referenceVoicePreflight, setReferenceVoicePreflight] = useState<ReferenceVoicePreflight | null>(null);
  const [referenceVoicePreflightError, setReferenceVoicePreflightError] = useState<string | null>(null);
  const [referenceVoicePreflightLoaded, setReferenceVoicePreflightLoaded] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const selectedScoreAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) ?? null,
    [agents, selectedAgentId],
  );
  const [showSimulateEvidenceOptions, setShowSimulateEvidenceOptions] = useState(false);
  const [includeStructuredEvidence, setIncludeStructuredEvidence] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [catalogReloadKey, setCatalogReloadKey] = useState(0);
  const suiteLoadRequestRef = useRef(0);

  const selectedSuite = useMemo(
    () => suites.find((suite) => suite.id === selectedSuiteId) ?? suites[0] ?? null,
    [selectedSuiteId, suites],
  );
  const selectedScenario = useMemo(
    () => selectedSuite?.scenarios.find((scenario) => scenario.id === selectedScenarioId) ?? selectedSuite?.scenarios[0] ?? null,
    [selectedScenarioId, selectedSuite],
  );
  const supportsSuiteExecutionScope = Boolean(
    selectedScoreAgent?.channel === 'text'
    && !isSavedReplayTargetId(selectedScoreAgent.target),
  );
  const matchingProductProjects = useMemo(
    () => productProjects.filter((project) => project.project_id === projectId),
    [productProjects, projectId],
  );
  function clearStructuredEvidenceFields() {
    setActionTrace('');
    setFinalState('');
    setCallEvidence('');
    setGroupCall('');
    setVconEvidence('');
    setIncludeStructuredEvidence(false);
  }

  function onTranscriptChange(nextValue: string) {
    preserveScoreEvidenceRef.current = true;
    setTranscript(nextValue);
    if (view !== 'score') return;
    setReport(null);
    setJudgeGate(null);

    const hadStructured =
      !isBlankJsonField(actionTrace)
      || !isBlankJsonField(finalState)
      || Boolean(callEvidence.trim())
      || Boolean(groupCall.trim())
      || Boolean(vconEvidence.trim())
      || includeStructuredEvidence;
    if (!hadStructured) {
      if (!nextValue.trim()) {
        setUploadMessage('Transcript cleared. Paste a transcript or load sample evidence to score again.');
      }
      return;
    }

    clearStructuredEvidenceFields();
    setUploadMessage(
      nextValue.trim()
        ? 'Cleared structured sample evidence after the transcript changed, so Evaluate scores your edited transcript.'
        : 'Cleared structured sample evidence because the transcript is empty. Reload sample evidence or paste a transcript to score again.',
    );
  }

  function applyScenarioStructuredSample(nextScenario: BenchmarkScenario) {
    setActionTrace(stringifyEditable(nextScenario.sample_action_trace, '[]'));
    setFinalState(stringifyEditable(nextScenario.sample_final_state ?? nextScenario.expected_final_state, '{}'));
    setCallEvidence('');
    setGroupCall('');
    setVconEvidence(
      nextScenario.sample_transcript ? sampleVconFromTranscript(nextScenario.sample_transcript) : '',
    );
  }

  function loadScenarioStarterData(
    nextScenario = selectedScenario,
    options: { includeStructuredSample?: boolean } = {},
  ) {
    if (!nextScenario) return;

    const nextTranscript = nextScenario.sample_transcript ?? '';
    const includeStructuredSample = options.includeStructuredSample === true;
    setTranscript(nextTranscript);
    // On /eval, default to transcript-only so hidden sample traces cannot force Task/Final 100s.
    // Opt into full sample when the operator explicitly asks for structured measurement.
    if (view === 'score' && !includeStructuredSample) {
      setActionTrace('');
      setFinalState('');
      setCallEvidence('');
      setGroupCall('');
      setVconEvidence('');
      setIncludeStructuredEvidence(false);
    } else {
      applyScenarioStructuredSample(nextScenario);
      setIncludeStructuredEvidence(view === 'score' ? includeStructuredSample : false);
    }
    setReport(null);
    setSuiteSimulation(null);
    setSaveMessage(null);
    setJudgeGate(null);
    setCopyMessage(null);
    setRunError(null);
    setUploadMessage(null);
  }

  function onLoadSampleEvidence(scenario: BenchmarkScenario, options: { includeStructuredSample?: boolean } = {}) {
    preserveScoreEvidenceRef.current = true;
    setSelectedScenarioId(scenario.id);
    loadScenarioStarterData(scenario, options);
    setShowSimulateEvidenceOptions(false);
    const withStructured = options.includeStructuredSample === true;
    setUploadMessage(
      view === 'score'
        ? withStructured
          ? `Loaded full sample evidence: ${scenario.title}. Task completion and final state will be measured from the sample traces.`
          : `Loaded sample transcript: ${scenario.title}. Task/final stay n/a until you include structured evidence.`
        : `Loaded sample evidence: ${scenario.title}. This evidence is synthetic.`,
    );
  }

  function onToggleStructuredEvidence(checked: boolean) {
    setIncludeStructuredEvidence(checked);
    if (view !== 'score' || !checked || !selectedScenario) return;
    // Checking the box with empty fields should load the scenario sample traces so
    // Task completion / Final state become measurable instead of staying n/a forever.
    if (isBlankJsonField(actionTrace) && isBlankJsonField(finalState)) {
      applyScenarioStructuredSample(selectedScenario);
      setUploadMessage(
        `Included sample action trace and final state for ${selectedScenario.title}. Evaluate to measure task completion and final state.`,
      );
    }
  }

  async function onUploadEvidenceFile(file: File | null) {
    if (!file) return;
    preserveScoreEvidenceRef.current = true;
    setUploadMessage(null);
    setRunError(null);
    try {
      const text = await file.text();
      const loaded = describeUploadedEvidence(file.name, text);
      if (loaded.kind === 'vcon') {
        setVconEvidence(loaded.vcon || '');
        if (loaded.transcript) setTranscript(loaded.transcript);
        setCallEvidence('');
        // Keep the uploaded vCon available under structured evidence, but do not auto-include it on /eval.
        if (view === 'score') {
          setIncludeStructuredEvidence(false);
          setUploadMessage(
            `${loaded.message} Transcript was extracted for scoring. Check “Include structured evidence” if you also want the vCon artifact evaluated.`,
          );
          setReport(null);
          return;
        }
      } else {
        setTranscript(loaded.transcript || '');
        setVconEvidence('');
      }
      setReport(null);
      setUploadMessage(loaded.message);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Could not read the uploaded file.');
    }
  }

  useEffect(() => {
    const requestId = suiteLoadRequestRef.current + 1;
    suiteLoadRequestRef.current = requestId;
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timeoutId = window.setTimeout(() => controller?.abort(), 12000);

    async function loadSuites() {
      setIsLoading(true);
      setLoadError(null);

      try {
        const nextSuites = await fetchBenchmarkSuites(controller?.signal);
        if (suiteLoadRequestRef.current !== requestId) return;
        setSuites(nextSuites);
        const preset = readWorkflowDemoPreset();
        const presetSuite = preset ? nextSuites.find((suite) => suite.id === preset.suiteId) : null;
        const presetScenario = presetSuite?.scenarios.find((scenario) => scenario.id === preset?.scenarioId) ?? null;
        if (presetSuite && presetScenario) {
          setSelectedSuiteId(presetSuite.id);
          setSelectedScenarioId(presetScenario.id);
        } else {
          const preferred =
            view === 'run'
              ? nextSuites.find((suite) => suite.id === 'call-center-voice-ai') ?? nextSuites[0]
              : nextSuites[0];
          setSelectedSuiteId(preferred?.id ?? '');
          setSelectedScenarioId(preferred?.scenarios[0]?.id ?? '');
        }
        if (!nextSuites.length) {
          setLoadError('No benchmark suites are available from the API yet.');
        }
      } catch (err) {
        if (suiteLoadRequestRef.current !== requestId) return;
        setSuites([]);
        setSelectedSuiteId('');
        setSelectedScenarioId('');
        const aborted = typeof err === 'object' && err !== null && 'name' in err && (err as { name?: string }).name === 'AbortError';
        setLoadError(
          aborted
            ? 'Timed out loading benchmark suites. Check that the API is running and reachable.'
            : err instanceof Error
              ? err.message
              : 'Could not load benchmark suites',
        );
      } finally {
        window.clearTimeout(timeoutId);
        if (suiteLoadRequestRef.current === requestId) {
          setIsLoading(false);
        }
      }

      if (suiteLoadRequestRef.current !== requestId) return;
      try {
        const nextConfig = await fetchProductConfig();
        const nextOpenAI = await fetchOpenAIProviderStatus().catch(() => null);
        if (suiteLoadRequestRef.current !== requestId) return;
        setProductConfig(nextConfig);
        if (nextOpenAI) setOpenaiProvider(nextOpenAI);
      } catch {
        // Suites can still run without product config / OpenAI status.
      }
    }

    void loadSuites();

    return () => {
      controller?.abort();
      window.clearTimeout(timeoutId);
      if (suiteLoadRequestRef.current === requestId) {
        suiteLoadRequestRef.current = requestId + 1;
      }
    };
  }, [catalogReloadKey, view]);

  useEffect(() => {
    let active = true;
    async function loadExecutionModels() {
      if (openaiProvider?.status !== 'connected') {
        setExecutionModelOptions([DEFAULT_EXECUTION_MODEL, ...FALLBACK_EXECUTION_MODELS.filter((id) => id !== DEFAULT_EXECUTION_MODEL)]);
        setExecutionModelsMessage('Connect OpenAI to load GPT models; local Ollama models stay available.');
        setExecutionModelName((current) => current || DEFAULT_EXECUTION_MODEL);
        return;
      }
      try {
        const { models, message } = await fetchOpenAIModels();
        if (!active) return;
        setExecutionModelOptions(models);
        setExecutionModelsMessage(message);
        setExecutionModelName((current) => (models.includes(current) ? current : DEFAULT_EXECUTION_MODEL));
      } catch {
        if (!active) return;
        setExecutionModelOptions(FALLBACK_EXECUTION_MODELS);
        setExecutionModelsMessage('Using built-in model list. Re-connect OpenAI to refresh.');
        setExecutionModelName((current) => current || DEFAULT_EXECUTION_MODEL);
      }
    }
    void loadExecutionModels();
    return () => {
      active = false;
    };
  }, [openaiProvider?.status]);

  useEffect(() => {
    let active = true;
    setAgentsLoaded(false);
    listAgents()
      .then((next) => {
        if (!active) return;
        const availableAgents = view === 'run'
          ? next.filter((agent) => !isSavedReplayTargetId(agent.target))
          : next;
        setAgents(availableAgents);
        const params = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
        const fromQuery = params?.get('agent_id');
        const matched = fromQuery ? availableAgents.find((item) => item.id === fromQuery) : null;
        const fallback = view === 'run'
          ? availableAgents.find((agent) => agent.id === 'generalist-text-agent')
            ?? availableAgents.find((agent) => agent.id === 'mock-text-agent')
            ?? availableAgents[0]
            ?? null
          : availableAgents.find((agent) => agent.id === 'mock-text-agent') ?? availableAgents[0] ?? null;
        const selected = matched ?? fallback;
        const nextId = selected?.id || '';
        setSelectedAgentId(nextId);
        if (selected && (view !== 'score' || matched)) {
          applyAgentProfileDefaults(selected, { setAgentProfile, setModelName, setPromptVersion });
        }
        if (matched) {
          if (matched.target === 'builtin_sample_voice') {
            setExecutionMode('pipecat_webrtc');
            setExecutionTesterId('pipecat_tester');
            setExecutionExecutorId('cae_local_audio_loop');
          } else if (matched.target === 'pipecat_public_demo') {
            setExecutionMode('pipecat_webrtc');
            setExecutionTesterId('pipecat_tester');
            setExecutionExecutorId('pipecat_public_daily');
          } else if (matched.target === 'signalwire_holy_guacamole') {
            setExecutionMode('pipecat_webrtc');
            setExecutionTesterId('pipecat_tester');
            setExecutionExecutorId('signalwire_public_webrtc');
            setExecutionMaxExchanges(1);
          } else if (matched.target === 'voice_fixture') {
            setExecutionMode('voice_fixture');
            setExecutionTesterId('fixture_replay');
            setExecutionExecutorId('evidence_replay');
          } else if (matched.target === 'offline_acc_fixture') {
            setExecutionMode('text_callable');
            setExecutionTesterId('fixture_replay');
            setExecutionExecutorId('evidence_replay');
          } else if (isExternalVoiceTargetId(matched.target)) {
            setExecutionMode('text_callable');
            setExecutionTesterId('scenario_simulator');
            setExecutionExecutorId(
              matched.target === 'sip_agent'
                ? 'acc_sip'
                : matched.target === 'phone_agent'
                  ? 'acc_phone'
                  : 'acc_browser_webrtc',
            );
          } else {
            setExecutionMode('text_callable');
            setExecutionTesterId('scenario_simulator');
            setExecutionExecutorId('local_async_runner');
          }
        }
        setAgentsLoaded(true);
      })
      .catch(() => {
        if (!active) return;
        setAgents([]);
        setAgentsLoaded(true);
      });
    return () => {
      active = false;
    };
  }, [view]);

  useEffect(() => {
    if (view !== 'run') return;
    let active = true;
    setReferenceVoicePreflightLoaded(false);
    fetchReferenceVoicePreflight()
      .then((preflight) => {
        if (!active) return;
        setReferenceVoicePreflight(preflight);
        setReferenceVoicePreflightError(null);
        setReferenceVoicePreflightLoaded(true);
      })
      .catch((error) => {
        if (!active) return;
        setReferenceVoicePreflight(null);
        setReferenceVoicePreflightError(error instanceof Error ? error.message : 'Voice preflight unavailable.');
        setReferenceVoicePreflightLoaded(true);
      });
    return () => {
      active = false;
    };
  }, [view]);

  useEffect(() => {
    if (view !== 'run' || typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    if (params.get('launch') !== 'demo' && !params.get('agent_id')) return;
    document.getElementById('launch-agent')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [view]);

  useEffect(() => {
    if (view !== 'run' || typeof window === 'undefined') return;
    const waitingForVoicePreflight = (
      selectedScoreAgent?.target === 'builtin_sample_voice'
      && !referenceVoicePreflightLoaded
    );
    if (
      autoLaunchDemoRef.current
      || isLoading
      || isLaunchingExecution
      || !selectedSuite
      || !agentsLoaded
      || !openaiProvider
      || waitingForVoicePreflight
    ) return;

    const params = new URLSearchParams(window.location.search);
    if (params.get('launch') !== 'demo') return;

    const wantedAgentId = params.get('agent_id');
    if (!selectedAgentId || (wantedAgentId && selectedAgentId !== wantedAgentId)) return;

    if (executionMode === 'text_callable' && !selectedScenario) return;

    autoLaunchDemoRef.current = true;
    setExecutionMessage('Starting try-it-out run…');
    void onLaunchExecution({
      redirectToAnalysis: true,
    });
    // Intentionally omit onLaunchExecution: including it retriggers auto-launch on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    view,
    isLoading,
    isLaunchingExecution,
    selectedSuite,
    selectedScenario,
    selectedAgentId,
    agentsLoaded,
    executionMode,
    openaiProvider,
    referenceVoicePreflightLoaded,
    selectedScoreAgent?.target,
  ]);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const storedUser = window.localStorage.getItem('conversation-evals-demo-user');
    const storedProject = window.localStorage.getItem('conversation-evals-demo-project');
    const storedPlan = window.localStorage.getItem('conversation-evals-demo-plan') as PricingPlan['id'] | null;

    const nextUser = storedUser || `demo-user-${Math.random().toString(36).slice(2, 8)}`;
    const nextProject = storedProject || 'call-center-demo';
    const nextPlan = storedPlan && ['free', 'starter', 'team', 'business'].includes(storedPlan)
      ? storedPlan
      : 'free';

    window.localStorage.setItem('conversation-evals-demo-user', nextUser);
    window.localStorage.setItem('conversation-evals-demo-project', nextProject);
    window.localStorage.setItem('conversation-evals-demo-plan', nextPlan);

    setUserId(nextUser);
    setProjectId(nextProject);
    setPlan(nextPlan);
  }, []);

  useEffect(() => {
    if (!userId) {
      setProductProjects([]);
      setProductProjectId('');
      return;
    }
    let active = true;
    listProductProjects(userId)
      .then((projects) => {
        if (!active) return;
        setProductProjects(projects);
        const matching = projects.filter((project) => project.project_id === projectId);
        const stored = window.localStorage.getItem('conversation-evals-demo-product-project-id') || '';
        const selected = matching.some((project) => project.id === stored)
          ? stored
          : matching.length === 1
            ? matching[0].id
            : '';
        setProductProjectId(selected);
      })
      .catch(() => {
        if (!active) return;
        setProductProjects([]);
        setProductProjectId('');
      });
    return () => {
      active = false;
    };
  }, [projectId, userId]);

  useEffect(() => {
    if (!userId) {
      setSavedRuns([]);
      setSuiteSavedRuns([]);
      setAuditEvents([]);
      setSuiteRuns([]);
      setProjectRegressionSummary(null);
      setScenarioRegressionSummary(null);
      return;
    }
    if (!selectedSuite?.id || !selectedScenario?.id) return;

    let isMounted = true;
    Promise.all([
      listSavedRuns(userId, projectId, selectedSuite.id, selectedScenario.id),
      listSavedRuns(userId, projectId, selectedSuite.id).catch(() => []),
      listAuditEvents(userId, projectId).catch(() => []),
      listBenchmarkSuiteRuns(userId, projectId, selectedSuite.id, suiteRunStatusFilter).catch(() => []),
    ])
      .then(async ([runs, nextSuiteSavedRuns, events, nextSuiteRuns]) => {
        const [summary, scenarioSummary] = await Promise.all([
          nextSuiteSavedRuns.length ? fetchProjectRegressionSummary(userId, projectId).catch(() => null) : Promise.resolve(null),
          runs.length
            ? fetchProjectRegressionSummary(userId, projectId, selectedSuite.id, selectedScenario.id).catch(() => null)
            : Promise.resolve(null),
        ]);
        if (!isMounted) return;
        setSavedRuns(runs);
        setSuiteSavedRuns(nextSuiteSavedRuns);
        setAuditEvents(events);
        setSuiteRuns((current) => mergeSuiteRunRecords(current, nextSuiteRuns, selectedSuite.id, suiteRunStatusFilter));
        setProjectRegressionSummary(summary);
        setScenarioRegressionSummary(scenarioSummary);
      })
      .catch(() => {
        if (!isMounted) return;
        setSavedRuns([]);
        setSuiteSavedRuns([]);
        setAuditEvents([]);
        setSuiteRuns([]);
        setProjectRegressionSummary(null);
        setScenarioRegressionSummary(null);
      });

    return () => {
      isMounted = false;
    };
  }, [projectId, selectedScenario?.id, selectedSuite?.id, suiteRunStatusFilter, userId]);

  useEffect(() => {
    if (!selectedSuite) return;
    setSelectedScenarioId((current) => (
      selectedSuite.scenarios.some((scenario) => scenario.id === current) ? current : selectedSuite.scenarios[0]?.id ?? ''
    ));
  }, [selectedSuite]);

  useEffect(() => {
    if (!selectedSuite?.id) {
      setContractManifest(null);
      setContractManifestError(null);
      return;
    }

    let isMounted = true;
    setContractManifest(null);
    setContractManifestError(null);

    fetchBenchmarkSuiteContractManifest(selectedSuite.id)
      .then((manifest) => {
        if (isMounted) setContractManifest(manifest);
      })
      .catch((err) => {
        if (!isMounted) return;
        setContractManifestError(err instanceof Error ? err.message : 'Could not load suite contract manifest.');
      });

    return () => {
      isMounted = false;
    };
  }, [selectedSuite?.id]);

  useEffect(() => {
    if (!selectedScenario) return;

    if (loadingSavedRunRef.current) {
      loadingSavedRunRef.current = false;
      return;
    }

    const preloadSample = view !== 'score' || shouldPreloadSampleEvidence();
    if (view === 'score' && !preloadSample && preserveScoreEvidenceRef.current) return;
    setTranscript(preloadSample ? selectedScenario.sample_transcript ?? '' : '');
    // /eval scores the transcript by default. Do not preload sample action/final-state traces —
    // they complete every required action and keep scores at 100 after transcript edits.
    if (view === 'score') {
      setActionTrace('');
      setFinalState('');
      setIncludeStructuredEvidence(false);
    } else {
      setActionTrace(preloadSample ? stringifyEditable(selectedScenario.sample_action_trace, '[]') : '');
      setFinalState(preloadSample ? stringifyEditable(selectedScenario.sample_final_state ?? selectedScenario.expected_final_state, '{}') : '');
    }
    setCallEvidence('');
    setGroupCall('');
    setVconEvidence('');
    setReport(null);
    setSuiteSimulation(null);
    setSaveMessage(null);
    setJudgeGate(null);
    setCopyMessage(null);
    setRunError(null);
    setUploadMessage(preloadSample && view === 'score'
      ? `Loaded sample transcript for ${selectedScenario.title}. This evidence is synthetic.`
      : null);
  }, [selectedScenario, view]);

  useEffect(() => {
    if (!userId || !projectId || !selectedSuite?.id) return;
    const hasActiveSuiteRun = suiteRuns.some((run) => isActiveSuiteRunStatus(run.status));
    if (!hasActiveSuiteRun) return;

    const interval = window.setInterval(() => {
      listBenchmarkSuiteRuns(userId, projectId, selectedSuite.id, suiteRunStatusFilter)
        .then((nextSuiteRuns) => {
          setSuiteRuns((current) => mergeSuiteRunRecords(current, nextSuiteRuns, selectedSuite.id, suiteRunStatusFilter));
        })
        .catch(() => undefined);
    }, 4000);

    return () => window.clearInterval(interval);
  }, [projectId, selectedSuite?.id, suiteRunStatusFilter, suiteRuns, userId]);

  const activeExecutionRunId = executionRun && isActiveExecutionStatus(executionRun.status)
    ? executionRun.execution_run_id
    : null;

  useEffect(() => {
    if (!userId || !activeExecutionRunId) return;
    const executionRunId = activeExecutionRunId;
    let active = true;
    let timer: number | undefined;

    async function poll() {
      fetchExecutionRun(userId, executionRunId)
        .then((next) => {
          if (!active) return;
          setExecutionRun(next);
          onExecutionUpdated?.(next);
          if (!isActiveExecutionStatus(next.status)) {
            const completed = next.progress?.completed_conversations ?? 0;
            const total = next.progress?.total_conversations ?? 0;
            setExecutionMessage(
              next.status === 'failed'
                ? `Execution failed${next.error ? `: ${next.error}` : '.'}`
                : `Execution ${next.status}. Captured ${completed}/${total} conversations${
                    next.inference_set_path ? ` → ${next.inference_set_path}` : ''
                  }.`,
            );
          } else {
            timer = window.setTimeout(() => void poll(), 1200);
          }
        })
        .catch(() => {
          if (active) timer = window.setTimeout(() => void poll(), 2400);
        });
    }

    timer = window.setTimeout(() => void poll(), 1200);
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [activeExecutionRunId, onExecutionUpdated, userId]);

  function ensureDemoIdentity(): { userId: string; projectId: string; plan: PricingPlan['id'] } {
    const nextUser = userId || (typeof window !== 'undefined'
      ? window.localStorage.getItem('conversation-evals-demo-user')
      : null) || `demo-user-${Math.random().toString(36).slice(2, 8)}`;
    const nextProject = projectId || (typeof window !== 'undefined'
      ? window.localStorage.getItem('conversation-evals-demo-project')
      : null) || 'call-center-demo';
    const storedPlan = typeof window !== 'undefined'
      ? window.localStorage.getItem('conversation-evals-demo-plan') as PricingPlan['id'] | null
      : null;
    const nextPlan = plan || (storedPlan && ['free', 'starter', 'team', 'business'].includes(storedPlan) ? storedPlan : 'free');

    if (typeof window !== 'undefined') {
      window.localStorage.setItem('conversation-evals-demo-user', nextUser);
      window.localStorage.setItem('conversation-evals-demo-project', nextProject);
      window.localStorage.setItem('conversation-evals-demo-plan', nextPlan);
    }
    if (nextUser !== userId) setUserId(nextUser);
    if (nextProject !== projectId) setProjectId(nextProject);
    if (nextPlan !== plan) setPlan(nextPlan);
    return { userId: nextUser, projectId: nextProject, plan: nextPlan };
  }

  async function refreshAuditTrail(overrideUserId = userId, overrideProjectId = projectId) {
    if (!overrideUserId) return;
    try {
      setAuditEvents(await listAuditEvents(overrideUserId, overrideProjectId));
    } catch {
      setAuditEvents([]);
    }
  }

  async function onSaveRun() {
    if (!report) return;
    const identity = ensureDemoIdentity();
    const reportToSave =
      judgeGate?.status === 'ready'
        ? {
            ...report,
            llm_judge: {
              status: judgeGate.status,
              provider: judgeGate.provider ?? null,
              model: judgeGate.model ?? null,
              message: judgeGate.message,
              credits: judgeGate.credits,
              latency_ms: judgeGate.latency_ms ?? null,
              evidence_citations: judgeGate.evidence_citations,
              judge_output: judgeGate.judge_output ?? null,
              judge_result: judgeGate.judge_result ?? null,
              spend_control: judgeGate.spend_control ?? null,
              requested_at: new Date().toISOString(),
            },
          }
        : report;

    try {
      const saved = await saveBenchmarkRun({
        user_id: identity.userId,
        project_id: identity.projectId,
        plan: identity.plan,
        report: reportToSave,
        transcript,
      });
      setReport(reportToSave);
      setSavedRuns((current) => [saved, ...current.filter((run) => run.id !== saved.id)]);
      fetchProjectRegressionSummary(identity.userId, identity.projectId)
        .then(setProjectRegressionSummary)
        .catch(() => setProjectRegressionSummary(null));
      fetchProjectRegressionSummary(identity.userId, identity.projectId, saved.report.suite_id, saved.report.scenario_id)
        .then(setScenarioRegressionSummary)
        .catch(() => setScenarioRegressionSummary(null));
      await refreshAuditTrail(identity.userId, identity.projectId);
      setSaveMessage(
        judgeGate?.status === 'ready'
          ? `Saved run ${saved.id} to ${identity.projectId} (with LLM judge).`
          : `Saved run ${saved.id} to ${identity.projectId}.`,
      );
    } catch (err) {
      setSaveMessage(err instanceof Error ? err.message : 'Could not save this run.');
    }
  }

  async function onSaveSuiteRuns() {
    if (!suiteSimulation) return;
    const identity = ensureDemoIdentity();

    try {
      const saved = await Promise.all(
        suiteSimulation.scenario_runs.map((run) => saveBenchmarkRun({
          user_id: identity.userId,
          project_id: identity.projectId,
          plan: identity.plan,
          report: run.benchmark_report,
          transcript: run.transcript,
        })),
      );
      setSavedRuns((current) => [
        ...saved,
        ...current.filter((run) => !saved.some((savedRun) => savedRun.id === run.id)),
      ]);
      fetchProjectRegressionSummary(identity.userId, identity.projectId)
        .then(setProjectRegressionSummary)
        .catch(() => setProjectRegressionSummary(null));
      if (report?.suite_id && report.scenario_id) {
        fetchProjectRegressionSummary(identity.userId, identity.projectId, report.suite_id, report.scenario_id)
          .then(setScenarioRegressionSummary)
          .catch(() => setScenarioRegressionSummary(null));
      }
      listBenchmarkSuiteRuns(identity.userId, identity.projectId, suiteSimulation.suite_id)
        .then(setSuiteRuns)
        .catch(() => setSuiteRuns([]));
      await refreshAuditTrail(identity.userId, identity.projectId);
      setSaveMessage(`Saved ${saved.length} suite runs to ${identity.projectId}.`);
    } catch (err) {
      setSaveMessage(err instanceof Error ? err.message : 'Could not save this suite.');
    }
  }

  async function onConnectOpenAI() {
    setIsConnectingOpenAI(true);
    setOpenaiProviderMessage(null);
    try {
      const started = await startOpenAIProviderOAuth();
      if (started.authorize_url && typeof window !== 'undefined') {
        window.open(started.authorize_url, '_blank', 'noopener,noreferrer');
      }
      setOpenaiProviderMessage('Complete OpenAI login in the opened browser tab. This page will refresh when connected.');
      const deadline = Date.now() + 120_000;
      while (Date.now() < deadline) {
        await delay(2000);
        const status = await fetchOpenAIProviderStatus();
        setOpenaiProvider(status);
        if (status.status === 'connected') {
          setOpenaiProviderMessage(`Connected as ${status.email || status.account_id || 'OpenAI account'}.`);
          const nextConfig = await fetchProductConfig().catch(() => null);
          if (nextConfig) setProductConfig(nextConfig);
          const { models, message } = await fetchOpenAIModels().catch(() => ({
            models: FALLBACK_EXECUTION_MODELS,
            message: 'Using built-in model list. Re-connect OpenAI to refresh.',
          }));
          setExecutionModelOptions(models);
          setExecutionModelsMessage(message);
          setExecutionModelName((current) => (models.includes(current) ? current : DEFAULT_EXECUTION_MODEL));
          break;
        }
      }
    } catch (err) {
      setOpenaiProviderMessage(err instanceof Error ? err.message : 'Could not start OpenAI OAuth.');
    } finally {
      setIsConnectingOpenAI(false);
    }
  }

  async function onDisconnectOpenAI() {
    setOpenaiProviderMessage(null);
    try {
      await disconnectOpenAIProvider();
      const status = await fetchOpenAIProviderStatus();
      setOpenaiProvider(status);
      setExecutionModelOptions([DEFAULT_EXECUTION_MODEL]);
      setExecutionModelsMessage('Connect OpenAI to load models');
      setExecutionModelName(DEFAULT_EXECUTION_MODEL);
      const nextConfig = await fetchProductConfig().catch(() => null);
      if (nextConfig) setProductConfig(nextConfig);
      setOpenaiProviderMessage('OpenAI disconnected.');
    } catch (err) {
      setOpenaiProviderMessage(err instanceof Error ? err.message : 'Could not disconnect OpenAI.');
    }
  }

  async function onJudge() {
    if (!report) return;
    setIsJudging(true);
    setShowJudgePrompt(false);
    try {
      const next = await requestJudge({ plan, report, transcript, user_id: userId || undefined, project_id: userId ? projectId : undefined });
      setJudgeGate(next);
      if (next.status === 'ready') {
        setReport((current) =>
          current
            ? {
                ...current,
                llm_judge: {
                  status: next.status,
                  provider: next.provider ?? null,
                  model: next.model ?? null,
                  message: next.message,
                  credits: next.credits,
                  latency_ms: next.latency_ms ?? null,
                  evidence_citations: next.evidence_citations,
                  judge_output: next.judge_output ?? null,
                  judge_result: next.judge_result ?? null,
                  spend_control: next.spend_control ?? null,
                  requested_at: new Date().toISOString(),
                },
              }
            : current,
        );
      }
      await refreshAuditTrail();
    } catch (err) {
      setJudgeGate({
        status: 'blocked',
        required_plan: 'starter',
        credits: 10,
        message: err instanceof Error ? err.message : 'Judge request failed.',
        evidence_citations: [],
        block_reason: 'provider_error',
      });
    } finally {
      setIsJudging(false);
    }
  }

  async function onExportRun(runId: string) {
    if (!userId) return;

    try {
      const exported = await exportSavedRun(userId, runId);
      downloadJson(exported.filename, exported);
      await refreshAuditTrail();
      setExportMessage(`Exported ${exported.filename}.`);
    } catch (err) {
      setExportMessage(err instanceof Error ? err.message : 'Could not export this saved run.');
    }
  }

  async function onExportRunAuditArtifacts(run: SavedRun) {
    if (!userId) return;

    const benchmarkRunId = run.report.run_id;
    if (!benchmarkRunId && !run.artifacts?.audit_artifacts?.available) {
      setExportMessage('Saved run is missing a benchmark run id.');
      return;
    }

    try {
      let exported: BenchmarkRunAuditArtifactExport;
      try {
        exported = benchmarkRunId
          ? await exportBenchmarkRunAuditArtifacts(userId, benchmarkRunId)
          : buildSavedRunAuditArtifactExport(userId, run);
      } catch (err) {
        if (!run.artifacts?.audit_artifacts?.available) throw err;
        exported = buildSavedRunAuditArtifactExport(userId, run);
      }
      downloadJson(exported.filename, exported);
      setExportMessage(`Exported audit artifacts to ${exported.filename}.`);
    } catch (err) {
      setExportMessage(err instanceof Error ? err.message : 'Could not export audit artifacts for this run.');
    }
  }

  async function onExportProjectHistory() {
    if (!userId) return;

    try {
      const exported = await exportProjectHistory(userId, projectId, selectedSuite?.id, selectedScenario?.id);
      downloadJson(exported.filename, exported);
      await refreshAuditTrail();
      setExportMessage(`Exported ${exported.run_count} runs to ${exported.filename}. ${projectVconExportSummary(exported.vcon_export_summary)} ${projectContractArtifactSummary(exported.contract_artifact_summary)} ${scenarioCoverageExportSummary(exported.scenario_coverage_summary)}`);
    } catch (err) {
      setExportMessage(err instanceof Error ? err.message : 'Could not export this project history.');
    }
  }

  async function onExportBenchmarkRunHistory() {
    if (!userId) return;

    try {
      const exported = await exportBenchmarkRunHistory(userId, projectId, selectedSuite?.id, selectedScenario?.id);
      downloadJson(exported.filename, exported);
      setExportMessage(`Exported ${exported.run_count} benchmark runs to ${exported.filename}. ${benchmarkRunHistoryExportSummary(exported.summary)} ${scenarioCoverageExportSummary(exported.scenario_coverage_summary)} ${projectVconExportSummary(exported.vcon_export_summary)} ${projectContractArtifactSummary(exported.contract_artifact_summary)}`);
    } catch (err) {
      setExportMessage(err instanceof Error ? err.message : 'Could not export benchmark run history.');
    }
  }

  function onLoadSavedRun(run: SavedRun) {
    const willSwitchSelection = Boolean(
      (run.report.suite_id && run.report.suite_id !== selectedSuiteId)
      || (run.report.scenario_id && run.report.scenario_id !== selectedScenarioId),
    );
    loadingSavedRunRef.current = willSwitchSelection;
    if (run.report.suite_id) setSelectedSuiteId(run.report.suite_id);
    if (run.report.scenario_id) setSelectedScenarioId(run.report.scenario_id);
    setTranscript(run.transcript ?? run.report.transcript ?? '');
    setActionTrace(stringifyEditable(run.report.action_trace, '[]'));
    setFinalState(stringifyEditable(run.report.final_state, '{}'));
    setAgentVersion(run.report.run_metadata?.agent_version ?? '');
    setPromptVersion(run.report.run_metadata?.prompt_version ?? '');
    setModelName(run.report.run_metadata?.model_name ?? '');
    setRunNotes(run.report.run_metadata?.notes ?? '');
    setReport(run.report);
    setSuiteSimulation(null);
    setJudgeGate(null);
    setCopyMessage(null);
    setRunError(null);
    setSaveMessage(`Loaded saved run ${run.id} for review.`);
  }

  async function onRetrySavedRun(run: SavedRun) {
    const sourceReport = run.report;
    const suiteId = sourceReport.suite_id || selectedSuiteId;
    const scenarioId = sourceReport.scenario_id || selectedScenarioId;
    if (!suiteId || !scenarioId) {
      setRunError('Saved run is missing suite or scenario metadata.');
      return;
    }

    setIsRunning(true);
    setRunError(null);
    setCopyMessage(null);
    setSaveMessage(null);

    try {
      const nextAttempt = (sourceReport.run_lifecycle?.attempt ?? run.report.run_lifecycle?.next_attempt ?? 1) + 1;
      const nextReport = await runBenchmark({
        suite_id: suiteId,
        scenario_id: scenarioId,
        transcript: run.transcript ?? sourceReport.transcript ?? transcript,
        action_trace: sourceReport.action_trace ?? parseMaybeJson(actionTrace),
        final_state: sourceReport.final_state ?? parseMaybeJson(finalState),
        agent_version: sourceReport.run_metadata?.agent_version || agentVersion || undefined,
        prompt_version: sourceReport.run_metadata?.prompt_version || promptVersion || undefined,
        model_name: sourceReport.run_metadata?.model_name || modelName || undefined,
        notes: sourceReport.run_metadata?.notes || runNotes || undefined,
        user_id: userId || sourceReport.run_metadata?.user_id,
        project_id: projectId || sourceReport.run_metadata?.project_id,
        attempt: nextAttempt,
        max_attempts: Math.max(nextAttempt, 2),
        retry_of_run_id: sourceReport.run_id || run.id,
      });
      setSelectedSuiteId(suiteId);
      setSelectedScenarioId(scenarioId);
      setTranscript(run.transcript ?? sourceReport.transcript ?? transcript);
      setActionTrace(stringifyEditable(sourceReport.action_trace, actionTrace || '[]'));
      setFinalState(stringifyEditable(sourceReport.final_state, finalState || '{}'));
      setReport(nextReport);
      setSuiteSimulation(null);
      setJudgeGate(null);
      setSaveMessage(`Retried saved run ${run.id} as attempt ${nextAttempt}.`);
      if (userId) {
        listSavedRuns(userId, projectId, suiteId, scenarioId)
          .then(setSavedRuns)
          .catch(() => undefined);
      }
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Retry run failed');
    } finally {
      setIsRunning(false);
    }
  }

  function onLoadSuiteRun(run: BenchmarkSuiteRunRecord) {
    const suiteReport = run.suite_report;
    if (!suiteReport?.scenario_runs?.length) {
      setSaveMessage('Suite run artifacts are available after the retained run completes.');
      return;
    }

    const focusedRun = suiteReport.scenario_runs[0];
    const focusedReport = focusedRun.benchmark_report;
    setSelectedSuiteId(suiteReport.suite_id ?? run.suite_id);
    setSelectedScenarioId(focusedRun.scenario_id ?? focusedReport.scenario_id ?? '');
    setTranscript(focusedRun.transcript ?? focusedReport.transcript ?? '');
    setActionTrace(stringifyEditable(focusedRun.action_trace ?? focusedReport.action_trace, '[]'));
    setFinalState(stringifyEditable(focusedRun.final_state ?? focusedReport.final_state, '{}'));
    setAgentVersion(suiteReport.run_metadata?.agent_version ?? focusedReport.run_metadata?.agent_version ?? '');
    setPromptVersion(suiteReport.run_metadata?.prompt_version ?? focusedReport.run_metadata?.prompt_version ?? '');
    setModelName(suiteReport.run_metadata?.model_name ?? focusedReport.run_metadata?.model_name ?? '');
    setRunNotes(suiteReport.run_metadata?.notes ?? focusedReport.run_metadata?.notes ?? '');
    setSuiteSimulation({
      suite_run_id: suiteReport.suite_run_id ?? run.suite_run_id,
      suite_id: suiteReport.suite_id ?? run.suite_id,
      suite_name: suiteReport.suite_name,
      provider: suiteReport.provider,
      scenario_count: suiteReport.scenario_count ?? run.scenario_count,
      pass_count: suiteReport.pass_count ?? run.pass_count,
      needs_review_count: suiteReport.needs_review_count ?? run.needs_review_count,
      average_score: suiteReport.average_score ?? run.average_score,
      verdict: suiteReport.verdict ?? run.status,
      run_metadata: suiteReport.run_metadata,
      reliability_metrics: suiteReport.reliability_metrics ?? run.reliability_metrics,
      scenario_runs: suiteReport.scenario_runs,
      vcon_export: suiteReport.vcon_export,
    });
    setReport(focusedReport);
    setJudgeGate(null);
    setCopyMessage(null);
    setRunError(null);
    setSaveMessage(`Loaded retained suite run ${run.suite_run_id}.`);
  }

  async function onExportBenchmarkSuiteRunHistory() {
    if (!userId) return;

    try {
      const exported = await exportBenchmarkSuiteRunHistory(userId, projectId, selectedSuite?.id, suiteRunStatusFilter);
      downloadJson(exported.filename, exported);
      setExportMessage(`Exported ${exported.suite_run_count} suite runs to ${exported.filename}. ${suiteHistoryExportSummary(exported.summary)} ${scenarioCoverageExportSummary(exported.scenario_coverage_summary)} ${projectVconExportSummary(exported.vcon_export_summary)} ${projectContractArtifactSummary(exported.suite_contract_artifact_summary)}`);
    } catch (err) {
      setExportMessage(err instanceof Error ? err.message : 'Could not export suite run history.');
    }
  }

  function onSelectRecommendedScenario() {
    const nextScenarioId = suiteScenarioCoverage?.recommended_next_scenario?.id;
    if (!nextScenarioId || nextScenarioId === selectedScenario?.id) return;
    setSelectedScenarioId(nextScenarioId);
    setSaveMessage(`Switched to the next uncovered scenario: ${suiteScenarioCoverage?.recommended_next_scenario?.title ?? nextScenarioId}.`);
  }

  function onSelectCoverageScenario(scenarioId: string, scenarioTitle?: string) {
    if (!scenarioId || scenarioId === selectedScenario?.id) return;
    setSelectedScenarioId(scenarioId);
    setSaveMessage(`Focused uncovered scenario: ${scenarioTitle ?? scenarioId}.`);
  }

  async function onRefreshSuiteRuns() {
    if (!userId || !selectedSuite?.id) return;

    setIsRefreshingSuiteRuns(true);
    try {
      const refreshed = await listBenchmarkSuiteRuns(userId, projectId, selectedSuite.id, suiteRunStatusFilter);
      const merged = mergeSuiteRunRecords(suiteRuns, refreshed, selectedSuite.id, suiteRunStatusFilter);
      setSuiteRuns(merged);
      setSaveMessage(`Refreshed ${merged.length} suite runs for ${selectedSuite.title}.`);
    } catch (err) {
      setSaveMessage(err instanceof Error ? err.message : 'Could not refresh suite runs.');
    } finally {
      setIsRefreshingSuiteRuns(false);
    }
  }

  async function onExportRetainedSuiteAuditArtifacts(suiteRunId: string) {
    if (!userId) return;

    try {
      const exported = await exportBenchmarkSuiteRunAuditArtifacts(userId, suiteRunId);
      downloadJson(exported.filename, exported);
      const summary = exported.operator_summary;
      setExportMessage(`Exported suite audit artifacts for ${summary?.ready_scenarios ?? 0} ready scenarios to ${exported.filename}.`);
    } catch (err) {
      setExportMessage(err instanceof Error ? err.message : 'Could not export this suite audit artifact bundle.');
    }
  }

  async function onExportRetainedSuiteVconBundle(suiteRunId: string) {
    if (!userId) return;

    try {
      const exported = await exportBenchmarkSuiteRunVconBundle(userId, suiteRunId);
      downloadJson(exported.filename, exported);
      setExportMessage(`Exported ${exported.record_count} retained suite vCon records to ${exported.filename}.`);
    } catch (err) {
      setExportMessage(err instanceof Error ? err.message : 'Could not export this suite vCon bundle.');
    }
  }

  function onExportCurrentVcon() {
    if (!report?.vcon_export) return;
    const filenameParts = ['agentbench', report.suite_id, report.scenario_id, report.run_id, 'vcon']
      .filter(Boolean)
      .map(slugFilenamePart);
    downloadJson(`${filenameParts.join('-') || 'agentbench-vcon'}.json`, report.vcon_export);
    setExportMessage('Exported vCon-compatible benchmark record.');
  }

  function onExportCurrentReport() {
    if (!report) return;
    const filenameParts = ['agentbench', report.suite_id, report.scenario_id, report.run_id, 'report']
      .filter(Boolean)
      .map(slugFilenamePart);

    downloadJson(`${filenameParts.join('-') || 'agentbench-report'}.json`, {
      report,
      transcript: report.transcript ?? transcript,
      action_trace: report.action_trace ?? parseMaybeJson(actionTrace),
      final_state: report.final_state ?? parseMaybeJson(finalState),
      exported_at: new Date().toISOString(),
    });
    setExportMessage('Exported current benchmark report JSON.');
  }

  function onExportSuiteVconBundle() {
    if (!suiteSimulation) return;
    const records = [suiteSimulation.vcon_export, ...suiteSimulation.scenario_runs
      .map((run) => run.benchmark_report.vcon_export)
    ].filter((record): record is JsonRecord => Boolean(record));
    if (!records.length) {
      setExportMessage('No vCon-compatible records are available for this suite run.');
      return;
    }

    const filenameParts = ['agentbench', suiteSimulation.suite_id, suiteSimulation.suite_run_id, 'vcon-bundle']
      .filter(Boolean)
      .map((part) => String(part).replace(/[^a-z0-9-]+/gi, '-').replace(/^-+|-+$/g, '').toLowerCase());
    downloadJson(`${filenameParts.join('-') || 'agentbench-suite-vcon-bundle'}.json`, {
      suite_run_id: suiteSimulation.suite_run_id,
      suite_id: suiteSimulation.suite_id,
      suite_name: suiteSimulation.suite_name,
      exported_at: new Date().toISOString(),
      record_count: records.length,
      records,
    });
    setExportMessage(`Exported ${records.length} vCon-compatible suite records.`);
  }

  async function evaluateEvidence() {
    if (!selectedSuite || !selectedScenario) return;

    if (view === 'score' && !transcript.trim()) {
      setRunError('Add a transcript before evaluating. Clearing or editing the transcript also clears structured sample evidence so leftover traces cannot score a pass.');
      setReport(null);
      return;
    }

    setIsRunning(true);
    setRunError(null);
    setReport(null);
    setSuiteSimulation(null);
    setCopyMessage(null);

    try {
      const runMetadata = cleanRunMetadata({
        agent_version: agentVersion || agentProfile || undefined,
        prompt_version: promptVersion,
        model_name: modelName,
        notes: [
          agentProfile.trim() ? `agent_target=${agentProfile.trim()}` : '',
          runNotes.trim(),
        ].filter(Boolean).join(' · ') || undefined,
        user_id: userId || undefined,
        project_id: projectId || undefined,
      });
      const useStructured = view !== 'score' || includeStructuredEvidence;
      const nextReport = await runBenchmark({
        suite_id: selectedSuite.id,
        scenario_id: selectedScenario.id,
        transcript,
        final_state: useStructured && !isBlankJsonField(finalState) ? parseMaybeJson(finalState) : undefined,
        action_trace: useStructured && !isBlankJsonField(actionTrace) ? parseMaybeJson(actionTrace) : undefined,
        call: useStructured && callEvidence.trim() ? parseMaybeJson(callEvidence) : undefined,
        group_call: useStructured && groupCall.trim() ? parseMaybeJson(groupCall) : undefined,
        // Keep vCon behind the same opt-in on /eval so a leftover sample/upload record cannot force provenance.
        vcon: useStructured && vconEvidence.trim() ? parseMaybeJson(vconEvidence) as JsonRecord : undefined,
        ...runMetadata,
      });
      setReport(nextReport);
      setSuiteSimulation(null);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Benchmark run failed');
    } finally {
      setIsRunning(false);
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await evaluateEvidence();
  }

  async function onSimulate() {
    if (!selectedSuite || !selectedScenario) return;

    setIsSimulating(true);
    setRunError(null);
    setReport(null);
    setCopyMessage(null);

    try {
      const runMetadata = cleanRunMetadata({
        agent_version: agentVersion,
        prompt_version: promptVersion,
        model_name: modelName,
        notes: runNotes,
        user_id: userId || undefined,
        project_id: projectId || undefined,
      });
      const simulation = await simulateBenchmark({
        suite_id: selectedSuite.id,
        scenario_id: selectedScenario.id,
        agent_profile: agentProfile,
        include_failure: includeFailure,
        ...runMetadata,
      });
      setTranscript(simulation.transcript);
      setActionTrace(stringifyEditable(simulation.action_trace, '[]'));
      setFinalState(stringifyEditable(simulation.final_state, '{}'));
      setReport(simulation.benchmark_report);
      setSuiteSimulation(null);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Scenario simulation failed');
    } finally {
      setIsSimulating(false);
    }
  }

  async function onLaunchExecution(options?: { redirectToAnalysis?: boolean }) {
    if (!selectedSuite) return null;
    const identity = ensureDemoIdentity();

    if (matchingProductProjects.length > 1 && !productProjectId) {
      setExecutionMessage('Select the personal or workspace project for this run.');
      return null;
    }

    if (!selectedScoreAgent) {
      setExecutionMessage('Select an agent target before launching.');
      return null;
    }
    const supportsConfigurableExchanges =
      selectedScoreAgent.target === 'openai_codex'
      || selectedScoreAgent.target === 'builtin_sample_voice'
      || selectedScoreAgent.target === 'pipecat_public_demo'
      || selectedScoreAgent.target === 'signalwire_holy_guacamole';
    if (supportsConfigurableExchanges && executionMaxExchanges === '') {
      const exchangeLimit = selectedScoreAgent.target === 'signalwire_holy_guacamole' ? 2 : 10;
      setExecutionMessage(`Enter a maximum exchange count from 1 to ${exchangeLimit} before launching.`);
      return null;
    }
    const maxExchanges = executionMaxExchanges === '' ? 3 : executionMaxExchanges;
    if (selectedScoreAgent.target === 'openai_codex'
      && selectedScoreAgent.id !== 'generalist-text-agent'
      && openaiProvider?.status !== 'connected') {
      setExecutionMessage('Connect OpenAI before running this target.');
      return null;
    }
    if (isExternalVoiceTargetId(selectedScoreAgent.target)) {
      setExecutionMessage(
        'This ACC destination is not executable from CAE yet. Readiness checks do not replace the missing launch and evidence-capture adapter.',
      );
      return null;
    }
    const sampleVoiceAgent = selectedScoreAgent.target === 'builtin_sample_voice';
    if (sampleVoiceAgent && !referenceVoicePreflight?.ready) {
      const blockers = referenceVoicePreflight?.dependencies
        .filter((item) => !item.ready)
        .map((item) => item.detail)
        .join(' ');
      setExecutionMessage(
        blockers || referenceVoicePreflightError || 'Voice dependency preflight is unavailable. Resolve it before queueing.',
      );
      return null;
    }
    const legacyVoiceReplay = selectedScoreAgent.target === 'voice_fixture';
    const publicPipecatAgent = selectedScoreAgent.target === 'pipecat_public_demo';
    const signalwireAgent = selectedScoreAgent.target === 'signalwire_holy_guacamole';
    const maxExchangesForRun = signalwireAgent ? Math.min(2, maxExchanges) : maxExchanges;
    const runMode = sampleVoiceAgent || publicPipecatAgent || signalwireAgent
      ? 'pipecat_webrtc'
      : legacyVoiceReplay
        ? 'voice_fixture'
        : 'text_callable';
    const runTextCallable = selectedScoreAgent?.target === 'offline_acc_fixture'
        ? 'offline_acc_fixture'
        : selectedScoreAgent?.target === 'openai_codex'
          ? 'openai_codex'
          : selectedScoreAgent?.target === 'http_endpoint'
            ? 'http_endpoint'
          : 'mock_agent';
    const runTesterId: NonNullable<ExecutionRunRecord['tester_id']> = executionTesterId;
    const runExecutorId: NonNullable<ExecutionRunRecord['executor_id']> = executionExecutorId;

    const offlineFixtureText =
      runMode === 'text_callable' && runTextCallable === 'offline_acc_fixture';
    // Only saved ACC replay paths are tied to the cancellation-rescue fixture.
    // The two-agent Pipecat runner receives the selected catalog scenario.
    const cancellationScopedRun = legacyVoiceReplay || offlineFixtureText;
    const voiceSuiteId = 'call-center-voice-ai';
    const suiteForRun = cancellationScopedRun ? voiceSuiteId : selectedSuite.id;
    const suiteScopedRun = executionScope === 'suite' && supportsSuiteExecutionScope;
    const scenarioIds = cancellationScopedRun
      ? ['cancellation-rescue']
      : suiteScopedRun
        ? selectedSuite.scenarios.map((scenario) => scenario.id)
        : selectedScenario
          ? [selectedScenario.id]
          : [];

    if (!scenarioIds.length) {
      setExecutionMessage('Select at least one scenario to execute.');
      return null;
    }

    const suiteNote =
      cancellationScopedRun && (selectedSuite.id !== voiceSuiteId || offlineFixtureText)
        ? `Using suite ${voiceSuiteId} / cancellation-rescue for this scoped run. `
        : '';

    setIsLaunchingExecution(true);
    setExecutionMessage(null);
    setRunError(null);

    try {
      const modelNameForExecutionRun = publicPipecatAgent
        ? undefined
        : signalwireAgent
          ? 'signalwire-ai-agent'
          : executionModelName || DEFAULT_EXECUTION_MODEL;
      const queued = await createExecutionRun({
        suite_id: suiteForRun,
        scenario_ids: scenarioIds,
        mode: runMode,
        text_callable: runMode === 'text_callable' ? runTextCallable : undefined,
        iterations: executionIterations,
        max_exchanges: maxExchangesForRun,
        duplex_timeout_seconds: sampleVoiceAgent || publicPipecatAgent || signalwireAgent ? executionDuplexTimeoutSeconds : undefined,
        user_id: identity.userId,
        project_id: identity.projectId,
        product_project_id: productProjectId || undefined,
        evaluate: true,
        agent_id: selectedAgentId || undefined,
        model_name: modelNameForExecutionRun,
        tester_id: runTesterId,
        executor_id: runExecutorId,
        audio_transport: publicPipecatAgent
          ? 'pipecat_daily_webrtc'
          : signalwireAgent
            ? 'signalwire_webrtc'
          : runMode === 'pipecat_webrtc'
            ? 'pipecat_small_webrtc'
            : 'none',
      });
      const queuedWithLaunchContext = {
        ...queued,
        agent_id: selectedAgentId || queued.agent_id || undefined,
        agent_name: selectedScoreAgent?.name || queued.agent_name || undefined,
        tester_id: runTesterId || queued.tester_id,
        executor_id: queued.executor_id || runExecutorId,
      };
      setExecutionRun(queuedWithLaunchContext);
      onExecutionCreated?.(queuedWithLaunchContext);
      setExecutionMessage(
        `${suiteNote || ''}Execution queued: ${selectedScoreAgent.name} driven by ${testerDisplayName(runTesterId)} through ${queued.executor_id || runExecutorId}. Open /runs/${queued.execution_run_id} for analysis when complete.`,
      );
      listExecutionRuns(identity.userId, identity.projectId).catch(() => undefined);
      if (options?.redirectToAnalysis && queued.execution_run_id) {
        window.location.assign(executionAnalysisHref(queued.execution_run_id));
      }
      return queued;
    } catch (err) {
      setExecutionMessage(err instanceof Error ? err.message : 'Could not launch execution.');
      return null;
    } finally {
      setIsLaunchingExecution(false);
    }
  }

  async function onSimulateSuite() {
    if (!selectedSuite) return;

    setIsSimulating(true);
    setRunError(null);
    setReport(null);
    setSuiteSimulation(null);
    setCopyMessage(null);

    try {
      const runMetadata = cleanRunMetadata({
        agent_version: agentVersion,
        prompt_version: promptVersion,
        model_name: modelName,
        notes: runNotes,
        user_id: userId || undefined,
        project_id: projectId || undefined,
      });
      const simulation = await simulateBenchmarkSuite({
        suite_id: selectedSuite.id,
        agent_profile: agentProfile,
        include_failure: includeFailure,
        ...runMetadata,
      });
      const focusedRun = simulation.scenario_runs.find((run) => (run.scenario_id ?? run.benchmark_report.scenario_id) === selectedScenario?.id)
        ?? simulation.scenario_runs[0]
        ?? null;
      setSuiteSimulation(simulation);
      if (userId) {
        listBenchmarkSuiteRuns(userId, projectId, selectedSuite.id, suiteRunStatusFilter)
          .then((nextSuiteRuns) => {
            setSuiteRuns((current) => mergeSuiteRunRecords(current, nextSuiteRuns, selectedSuite.id, suiteRunStatusFilter));
          })
          .catch(() => setSuiteRuns([]));
      }
      if (focusedRun) {
        setSelectedScenarioId(focusedRun.scenario_id ?? focusedRun.benchmark_report.scenario_id ?? '');
        setTranscript(focusedRun.transcript);
        setActionTrace(stringifyEditable(focusedRun.action_trace, '[]'));
        setFinalState(stringifyEditable(focusedRun.final_state, '{}'));
        setReport(focusedRun.benchmark_report);
      }
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Suite simulation failed');
    } finally {
      setIsSimulating(false);
    }
  }

  async function onEnqueueSuiteSimulation() {
    if (!selectedSuite) return;
    const identity = ensureDemoIdentity();

    setIsEnqueueingSuite(true);
    setRunError(null);
    setCopyMessage(null);

    try {
      const runMetadata = cleanRunMetadata({
        agent_version: agentVersion,
        prompt_version: promptVersion,
        model_name: modelName,
        notes: runNotes,
        user_id: identity.userId,
        project_id: identity.projectId || undefined,
      });
      const queued = await enqueueBenchmarkSuiteSimulation({
        suite_id: selectedSuite.id,
        agent_profile: agentProfile,
        include_failure: includeFailure,
        ...runMetadata,
      });
      setSuiteRuns((current) => [queued, ...current.filter((run) => run.suite_run_id !== queued.suite_run_id)]);
      setSaveMessage(`Queued suite run ${queued.suite_run_id} for ${identity.projectId}.`);
      if (suiteRunStatusFilter) setSuiteRunStatusFilter('');
      let latest = queued;
      for (let attempt = 0; attempt < 8 && isActiveSuiteRunStatus(latest.status); attempt += 1) {
        await delay(750);
        try {
          latest = await fetchBenchmarkSuiteRun(identity.userId, queued.suite_run_id);
          setSuiteRuns((current) => [latest, ...current.filter((run) => run.suite_run_id !== latest.suite_run_id)]);
        } catch {
          break;
        }
      }
      if (!isActiveSuiteRunStatus(latest.status)) {
        setSaveMessage(`Suite run ${queued.suite_run_id} finished as ${latest.status}.`);
      }
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Could not queue suite simulation.');
    } finally {
      setIsEnqueueingSuite(false);
    }
  }

  const evidence = report?.evidence_spans ?? report?.evidence ?? [];
  const score = report?.score ?? report?.overall_score;
  const evidenceCitations = report?.evidence_citations ?? [];
  const verdict = report?.verdict ?? report?.overall;
  const suiteBrief = suiteSimulation ? formatSuiteBrief(suiteSimulation) : '';
  const selectedScenarioContract = contractManifest?.scenario_contracts?.find((item) => item.scenario_id === selectedScenario?.id) ?? null;
  const selectedScenarioManifestFingerprint = selectedScenarioContract?.scenario_contract_sha256
    ? selectedScenarioContract.scenario_contract_sha256.slice(0, 12)
    : null;
  const suiteManifestFingerprint = contractManifest?.suite_contract_manifest_sha256
    ? contractManifest.suite_contract_manifest_sha256.slice(0, 12)
    : null;
  const reportSuiteManifestFingerprint = report?.suite_contract_manifest_sha256
    ? report.suite_contract_manifest_sha256.slice(0, 12)
    : suiteManifestFingerprint ?? 'Not captured';
  const scenarioContractFingerprint = report?.scenario_contract_sha256 ? report.scenario_contract_sha256.slice(0, 12) : selectedScenarioManifestFingerprint ?? 'Not captured';
  const hasTranscriptEvidence = Boolean(transcript.trim());
  const hasStructuredEvidence = Boolean(
    !isBlankJsonField(actionTrace)
    || !isBlankJsonField(finalState)
    || callEvidence.trim()
    || groupCall.trim()
    || vconEvidence.trim(),
  );
  // On /eval, transcript is the primary evidence. Structured sample fields alone must not
  // keep Evaluate enabled after the user clears the visible conversation.
  const hasRunnableEvidence = view === 'score'
    ? hasTranscriptEvidence
    : Boolean(hasTranscriptEvidence || hasStructuredEvidence);
  const hasSavedCurrentScenario = Boolean(
    selectedScenario?.id && savedRuns.some((run) => run.report.scenario_id === selectedScenario.id),
  );
  const currentRegressionDelta = useMemo(() => currentReportRegressionDelta(report, savedRuns), [report, savedRuns]);
  const suiteScenarioCoverage = useMemo(
    () => scenarioCoverageFromRuns(selectedSuite, suiteSavedRuns, report),
    [selectedSuite, suiteSavedRuns, report],
  );
  const actionPlan = report ? reportActionPlan(report, currentRegressionDelta, suiteScenarioCoverage) : null;
  const reportBrief = report
    ? formatReportBrief(report, selectedScenario?.title, currentRegressionDelta, evidenceCitations, suiteScenarioCoverage, actionPlan)
    : '';
  const onboardingSteps = [
    {
      title: 'Pick a scenario',
      detail: selectedScenario
        ? `${selectedScenario.title}. ${starterDataSummary(selectedScenario)}`
        : selectedSuite && !selectedSuite.scenarios.length
          ? `${selectedSuite.title} needs at least one scenario before you can run evidence checks.`
          : 'Choose the benchmark suite and scenario to test.',
      done: Boolean(selectedScenario),
      ready: Boolean(selectedScenario || (selectedSuite && !selectedSuite.scenarios.length)),
      actionLabel: selectedScenario ? 'Reload starter data' : selectedSuite?.scenarios[0] ? 'Pick first scenario' : null,
      action: selectedScenario
        ? () => loadScenarioStarterData()
        : selectedSuite?.scenarios[0]
          ? () => setSelectedScenarioId(selectedSuite.scenarios[0].id)
          : undefined,
      actionVariant: 'secondary' as const,
    },
    {
      title: 'Run evidence check',
      detail: report ? `Latest verdict: ${verdict ?? 'complete'}${score !== undefined ? ` at ${score}` : ''}.` : view === 'score'
        ? 'Evaluate the loaded or uploaded evidence against this scenario.'
        : 'Run the benchmark against captured evidence.',
      done: Boolean(report),
      ready: hasRunnableEvidence && !isRunning && !isSimulating,
      actionLabel: report ? 'Run again' : hasRunnableEvidence ? 'Run sample now' : selectedScenario ? 'Load starter data first' : null,
      action: hasRunnableEvidence || report
        ? view === 'score' ? () => void evaluateEvidence() : () => void onSimulate()
        : selectedScenario
          ? () => loadScenarioStarterData()
          : undefined,
      actionVariant: 'primary' as const,
      disabled: Boolean((hasRunnableEvidence || report) && (isRunning || isSimulating)),
    },
    {
      title: 'Save repeatable history',
      detail: hasSavedCurrentScenario
        ? `Focused history is tracking ${selectedScenario?.title ?? 'this scenario'}.`
        : 'Save the result to compare future prompt, model, and agent changes.',
      done: hasSavedCurrentScenario,
      ready: Boolean(report && userId),
      actionLabel: report && !hasSavedCurrentScenario
        ? 'Save this run'
        : hasSavedCurrentScenario && savedRuns.length
          ? 'Export saved history'
          : report
            ? 'Save this run'
            : null,
      action: report && !hasSavedCurrentScenario
        ? () => void onSaveRun()
        : hasSavedCurrentScenario && savedRuns.length
          ? () => void onExportProjectHistory()
          : report
            ? () => void onSaveRun()
            : undefined,
      actionVariant: 'secondary' as const,
      disabled: Boolean(!report || !userId),
    },
  ];

  const judgeProviderReady =
    openaiProvider?.status === 'connected' || productConfig?.llm_judge_status === 'enabled';

  return (
    <section style={{ display: 'grid', gap: 20 }}>
      {(view === 'all') ? (
        <section className="card openai-provider-panel" aria-label="OpenAI judge provider">
          <div className="openai-provider-control">
            <div>
              <p className="eyebrow">LLM judge</p>
              <h2 style={{ margin: '4px 0 0', fontSize: 22 }}>Connect OpenAI for the local judge</h2>
              <p style={{ margin: '8px 0 0', color: 'var(--muted)' }}>
                {openaiProvider?.status === 'connected'
                  ? `Connected${openaiProvider.email ? ` as ${openaiProvider.email}` : ''}${openaiProvider.plan_type ? ` (${openaiProvider.plan_type})` : ''}.`
                  : openaiProvider?.message || `Connect Codex-style OpenAI OAuth to unlock LLM judging on scored evidence${productConfig?.llm_judge_status ? ` (${productConfig.llm_judge_status})` : ''}.`}
              </p>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {openaiProvider?.status === 'connected' ? (
                <button type="button" className="secondary-link" onClick={() => void onDisconnectOpenAI()}>
                  Disconnect OpenAI
                </button>
              ) : (
                <button type="button" className="primary-link" disabled={isConnectingOpenAI} onClick={() => void onConnectOpenAI()}>
                  {isConnectingOpenAI ? 'Waiting for OpenAI…' : 'Connect OpenAI'}
                </button>
              )}
            </div>
            {openaiProviderMessage ? <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>{openaiProviderMessage}</p> : null}
          </div>
        </section>
      ) : null}

      {view === 'all' ? <section className="first-run-panel" aria-label="First run checklist">
        <div>
          <p className="eyebrow">First run checklist</p>
          <h2>Get from sample scenario to saved QA history.</h2>
        </div>
        <ol className="onboarding-steps">
          {onboardingSteps.map((step, index) => {
            const status = onboardingStatusLabel(step.done, step.ready);
            return (
              <li key={step.title} data-state={step.done ? 'done' : step.ready ? 'ready' : 'next'}>
                <span aria-hidden="true">{index + 1}</span>
                <div className="onboarding-step-copy">
                  <strong>{step.title}</strong>
                  <p>{step.detail}</p>
                  {step.actionLabel && step.action ? (
                    <button
                      type="button"
                      className={step.actionVariant === 'primary' ? 'primary-link onboarding-step-action' : 'secondary-link onboarding-step-action'}
                      onClick={step.action}
                      disabled={step.disabled}
                    >
                      {step.actionLabel}
                    </button>
                  ) : null}
                </div>
                <em aria-label={`${step.title}: ${status}`}>{status}</em>
              </li>
            );
          })}
        </ol>
      </section> : null}

      {/* /runs (view=run): omit suite/scenario contract panel — launch uses catalog defaults. */}
      {view !== 'run' ? (
      <form
        onSubmit={onSubmit}
        className="card"
        style={{ padding: 24, display: 'grid', gap: 18 }}
      >
        {loadError ? (
          <div style={{ border: '1px solid var(--error-border)', background: 'var(--error-bg)', color: 'var(--error-text)', borderRadius: 8, padding: 12, display: 'grid', gap: 8 }}>
            <span>{loadError}</span>
            <button type="button" className="secondary-link" onClick={() => setCatalogReloadKey((value) => value + 1)}>
              Retry loading suites
            </button>
          </div>
        ) : null}

        {view === 'score' ? (
          <div
            aria-label="Evaluation contract"
            style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, display: 'grid', gap: 12, background: 'var(--panel-alt)' }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
              <div style={{ minWidth: 0, flex: '1 1 240px' }}>
                <p className="eyebrow" style={{ margin: 0 }}>Evaluation contract</p>
                <p style={{ margin: '6px 0 0', color: 'var(--muted)', fontSize: 14, lineHeight: 1.45 }}>
                  Evidence is scored against this scenario&apos;s required actions, forbidden actions, and rubric — not against the optional structured fields below.
                </p>
              </div>
              <ApiAwareLink
                href="/scenarios?create=1"
                className="secondary-link"
                style={{ flex: '0 0 auto', whiteSpace: 'nowrap' }}
              >
                Create new scenario
              </ApiAwareLink>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
              <label style={{ display: 'grid', gap: 8 }}>
                <span style={{ fontWeight: 700 }}>Suite</span>
                <select
                  aria-label="Evaluation suite"
                  value={selectedSuite?.id ?? ''}
                  disabled={isLoading || !suites.length}
                  onChange={(event) => setSelectedSuiteId(event.target.value)}
                  style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                >
                  {suites.map((suite) => (
                    <option key={suite.id} value={suite.id}>{suite.title}</option>
                  ))}
                </select>
              </label>
              <label style={{ display: 'grid', gap: 8 }}>
                <span style={{ fontWeight: 700 }}>Scenario</span>
                <select
                  aria-label="Evaluation scenario"
                  value={selectedScenario?.id ?? ''}
                  disabled={isLoading || !selectedSuite?.scenarios.length}
                  onChange={(event) => setSelectedScenarioId(event.target.value)}
                  style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                >
                  {(selectedSuite?.scenarios ?? []).map((scenario) => (
                    <option key={scenario.id} value={scenario.id}>{scenario.title}</option>
                  ))}
                </select>
              </label>
            </div>
            {selectedScenario ? (
              <div style={{ display: 'grid', gap: 8 }}>
                <p style={{ margin: 0, fontSize: 14, lineHeight: 1.45 }} aria-label="Selected evaluation scenario">
                  <strong>{selectedScenario.title}</strong>
                  {selectedScenario.user_goal || selectedScenario.user_persona
                    ? ` — ${selectedScenario.user_goal || selectedScenario.user_persona}`
                    : ''}
                </p>
                <details>
                  <summary style={{ cursor: 'pointer', fontWeight: 700, fontSize: 14 }}>What this scenario checks</summary>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginTop: 10 }}>
                    <ScenarioList title="Required actions" items={toStringList(selectedScenario.required_actions)} />
                    <ScenarioList title="Forbidden actions" items={toStringList(selectedScenario.forbidden_actions)} />
                    <ScenarioList title="Constraints" items={toStringList(selectedScenario.constraints)} />
                  </div>
                </details>
              </div>
            ) : null}
          </div>
        ) : null}

        {view !== 'score' ? <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
          <label style={{ display: 'grid', gap: 8 }}>
            <span style={{ fontWeight: 700 }}>Benchmark suite</span>
            <select
              value={selectedSuite?.id ?? ''}
              disabled={isLoading || !suites.length}
              onChange={(event) => setSelectedSuiteId(event.target.value)}
              style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
            >
              {suites.map((suite) => (
                <option key={suite.id} value={suite.id}>{suite.title}</option>
              ))}
            </select>
          </label>

          <label style={{ display: 'grid', gap: 8 }}>
            <span style={{ fontWeight: 700 }}>Scenario</span>
            <select
              value={selectedScenario?.id ?? ''}
              disabled={isLoading || !selectedSuite?.scenarios.length}
              onChange={(event) => setSelectedScenarioId(event.target.value)}
              style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
            >
              {(selectedSuite?.scenarios ?? []).map((scenario) => (
                <option key={scenario.id} value={scenario.id}>{scenario.title}</option>
              ))}
            </select>
          </label>
        </div> : null}

        {view !== 'score' && isLoading ? <p style={{ margin: 0, color: 'var(--muted)' }}>Loading benchmark suites...</p> : null}

        {view === 'score' ? null : selectedScenario ? (
          <div
            aria-label="Selected scenario"
            style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, background: 'var(--panel-alt)', display: 'grid', gap: 10 }}
          >
            <div>
              <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 13 }}>{selectedScenario.domain ?? selectedSuite?.title}</p>
              <h3 style={{ margin: 0 }}>{selectedScenario.title}</h3>
            </div>
            <p style={{ margin: 0, color: 'var(--muted)', lineHeight: 1.5 }}>{selectedScenario.user_goal || selectedScenario.user_persona || 'No goal provided.'}</p>
            <details>
              <summary style={{ cursor: 'pointer', fontWeight: 800 }}>Scenario rubric</summary>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14, marginTop: 12 }}>
                <ScenarioList title="Required actions" items={toStringList(selectedScenario.required_actions)} />
                <ScenarioList title="Forbidden actions" items={toStringList(selectedScenario.forbidden_actions)} />
                <ScenarioList title="Constraints" items={toStringList(selectedScenario.constraints)} />
              </div>
            </details>
            <div aria-label="Suite contract manifest" style={{ borderTop: '1px solid var(--border)', paddingTop: 12, display: 'grid', gap: 8 }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                <strong>Contract manifest</strong>
                <span aria-label={`Scenario contract: ${selectedScenarioManifestFingerprint ?? 'loading'}`} style={{ color: 'var(--muted)', fontSize: 13 }}>
                  {selectedScenarioManifestFingerprint ? `Scenario ${selectedScenarioManifestFingerprint}` : 'Scenario hash loading'}
                </span>
                <span aria-label={`Suite scenarios: ${contractManifest?.scenario_count ?? selectedSuite?.scenarios.length ?? 0}`} style={{ color: 'var(--muted)', fontSize: 13 }}>
                  {contractManifest?.scenario_count ?? selectedSuite?.scenarios.length ?? 0} scenarios
                </span>
                <span aria-label={`Suite manifest: ${suiteManifestFingerprint ?? 'loading'}`} style={{ color: 'var(--muted)', fontSize: 13 }}>
                  {suiteManifestFingerprint ? `Suite ${suiteManifestFingerprint}` : 'Suite hash loading'}
                </span>
              </div>
              {contractManifest?.evidence_requirements ? (
                <p style={{ margin: 0, color: 'var(--muted)', lineHeight: 1.5 }}>
                  Required evidence: {(contractManifest.evidence_requirements.required_artifacts ?? []).join(', ') || 'not declared'}.
                  {' '}Scoring: {(contractManifest.evidence_requirements.scoring_dimensions ?? []).join(', ') || 'not declared'}.
                </p>
              ) : contractManifestError ? (
                <p style={{ margin: 0, color: 'var(--danger)' }}>{contractManifestError}</p>
              ) : (
                <p style={{ margin: 0, color: 'var(--muted)' }}>Loading suite fingerprints and evidence requirements...</p>
              )}
            </div>
          </div>
        ) : selectedSuite && !isLoading ? (
          <div
            aria-label="Suite setup guidance"
            style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, background: 'var(--panel-alt)', display: 'grid', gap: 8 }}
          >
            <strong>{selectedSuite.title} has no benchmark scenarios yet.</strong>
            <p style={{ margin: 0, color: 'var(--muted)', lineHeight: 1.5 }}>
              Add at least one scenario to run evidence checks, queue suite runs, and track coverage for this suite.
            </p>
          </div>
        ) : null}

        {view !== 'score' ? (
          <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, display: 'grid', gap: 14 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) auto', gap: 16, alignItems: 'end' }}>
              <label style={{ display: 'grid', gap: 8 }}>
                <span style={{ fontWeight: 700 }}>Agent profile</span>
                <input
                  value={agentProfile}
                  onChange={(event) => setAgentProfile(event.target.value)}
                  placeholder="mock text agent"
                  style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                />
              </label>
              <label
                style={{
                  minHeight: 46,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 10,
                  border: '1px solid var(--border)',
                  borderRadius: 8,
                  padding: '10px 12px',
                  fontWeight: 760,
                }}
              >
                <input
                  type="checkbox"
                  checked={includeFailure}
                  onChange={(event) => setIncludeFailure(event.target.checked)}
                />
                Failure baseline
              </label>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
              <label style={{ display: 'grid', gap: 8 }}>
                <span style={{ fontWeight: 700 }}>Agent version</span>
                <input
                  value={agentVersion}
                  onChange={(event) => setAgentVersion(event.target.value)}
                  placeholder="agent-v12"
                  style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                />
              </label>
              <label style={{ display: 'grid', gap: 8 }}>
                <span style={{ fontWeight: 700 }}>Prompt version</span>
                <input
                  value={promptVersion}
                  onChange={(event) => setPromptVersion(event.target.value)}
                  placeholder="prompt-2026-05-25"
                  style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                />
              </label>
              <label style={{ display: 'grid', gap: 8 }}>
                <span style={{ fontWeight: 700 }}>Model</span>
                <input
                  value={modelName}
                  onChange={(event) => setModelName(event.target.value)}
                  placeholder="gpt-4.1-mini"
                  style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                />
              </label>
              <label style={{ display: 'grid', gap: 8 }}>
                <span style={{ fontWeight: 700 }}>Notes</span>
                <input
                  value={runNotes}
                  onChange={(event) => setRunNotes(event.target.value)}
                  placeholder="tightened escalation policy"
                  style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                />
              </label>
            </div>
          </div>
        ) : null}

        {view === 'score' ? (
          <section className="score-upload-panel" aria-label="Evidence upload">
            <div className="score-upload-copy">
              <p className="eyebrow">Evidence intake</p>
              <h2>Upload a vCon or transcript</h2>
              <p>Drop in your own conversation artifact, or load clearly labeled sample evidence.</p>
            </div>
            <div className="score-upload-actions">
              <label className="score-upload-drop">
                <span>Upload vCon or transcript</span>
                <small>Accepts .vcon, .json, .txt, .md</small>
                <input
                  type="file"
                  accept=".vcon,.json,.txt,.md,application/json,text/plain,text/markdown"
                  aria-label="Upload vCon or transcript file"
                  onChange={(event) => {
                    const file = event.target.files?.[0] ?? null;
                    void onUploadEvidenceFile(file);
                    event.currentTarget.value = '';
                  }}
                />
              </label>
              <button
                type="button"
                className="score-upload-simulate"
                onClick={() => setShowSimulateEvidenceOptions((current) => !current)}
              >
                {showSimulateEvidenceOptions ? 'Hide sample options' : 'Load sample evidence'}
              </button>
            </div>
            {showSimulateEvidenceOptions ? (
              <div className="score-simulate-options" aria-label="Sample evidence options">
                <p>
                  Synthetic sample for{' '}
                  <strong>{selectedScenario?.title ?? 'the selected scenario'}</strong> — not a live agent run.
                  Transcript-only keeps Task/Final as n/a. Full sample includes action trace + final state so those tiles are measured.
                </p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                  <button
                    type="button"
                    className="secondary-link"
                    disabled={!selectedScenario}
                    onClick={() => selectedScenario && onLoadSampleEvidence(selectedScenario)}
                  >
                    Load sample transcript only
                  </button>
                  <button
                    type="button"
                    className="secondary-link"
                    disabled={!selectedScenario}
                    onClick={() => selectedScenario && onLoadSampleEvidence(selectedScenario, { includeStructuredSample: true })}
                  >
                    Load full sample (measure Task/Final)
                  </button>
                </div>
              </div>
            ) : null}
            {uploadMessage ? <p className="score-upload-message">{uploadMessage}</p> : null}
          </section>
        ) : null}

        {view === 'score' ? (
          <details aria-label="Score attribution">
            <summary style={{ cursor: 'pointer', fontWeight: 700 }}>Attribute this score</summary>
            <p style={{ margin: '10px 0 0', color: 'var(--muted)', fontSize: 14, lineHeight: 1.45 }}>
              Optional labels for the saved report (which agent target produced this evidence). These do not change how evidence is scored.
            </p>
            <div style={{ display: 'grid', gap: 12, marginTop: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
                <label style={{ display: 'grid', gap: 8 }}>
                  <span style={{ fontWeight: 700 }}>Attributed agent target</span>
                  <input
                    aria-label="Attributed agent target"
                    value={agentProfile}
                    onChange={(event) => setAgentProfile(event.target.value)}
                    placeholder="support-bot / mock text target"
                    style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                  />
                </label>
                <label style={{ display: 'grid', gap: 8 }}>
                  <span style={{ fontWeight: 700 }}>Attributed model</span>
                  <input
                    aria-label="Attributed model"
                    value={modelName}
                    onChange={(event) => setModelName(event.target.value)}
                    placeholder="gpt-4.1-mini"
                    style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                  />
                </label>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
                <label style={{ display: 'grid', gap: 8 }}>
                  <span style={{ fontWeight: 700 }}>Target version</span>
                  <input
                    aria-label="Attributed target version"
                    value={agentVersion}
                    onChange={(event) => setAgentVersion(event.target.value)}
                    placeholder="agent-v12"
                    style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                  />
                </label>
                <label style={{ display: 'grid', gap: 8 }}>
                  <span style={{ fontWeight: 700 }}>Prompt version</span>
                  <input
                    aria-label="Attributed prompt version"
                    value={promptVersion}
                    onChange={(event) => setPromptVersion(event.target.value)}
                    placeholder="prompt-2026-05-25"
                    style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                  />
                </label>
                <label style={{ display: 'grid', gap: 8 }}>
                  <span style={{ fontWeight: 700 }}>Notes</span>
                  <input
                    aria-label="Attribution notes"
                    value={runNotes}
                    onChange={(event) => setRunNotes(event.target.value)}
                    placeholder="tightened escalation policy"
                    style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white' }}
                  />
                </label>
              </div>
            </div>
          </details>
        ) : null}

        <details open={view === 'score' ? true : undefined}>
          <summary style={{ cursor: 'pointer', fontWeight: 800 }}>
            {view === 'score' ? 'Evidence payload (editable)' : 'Evidence payload'}
          </summary>
          <div style={{ display: 'grid', gap: 16, marginTop: 14 }}>
            <label style={{ display: 'grid', gap: 8 }}>
              <span style={{ fontWeight: 700 }}>Transcript</span>
              <textarea
                aria-label="Evidence transcript"
                value={transcript}
                onChange={(event) => onTranscriptChange(event.target.value)}
                rows={7}
                style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }}
              />
              {view === 'score' && !hasTranscriptEvidence ? (
                <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13, lineHeight: 1.4 }}>
                  Paste a transcript or load sample evidence to evaluate. Structured traces alone are not enough here.
                </p>
              ) : null}
            </label>

            <details className="eval-structured-evidence">
              <summary>Structured and channel evidence (optional)</summary>
              <p>
                {view === 'score'
                  ? 'Hidden by default from Evaluate on this page. Sample transcript scoring ignores these unless you opt in below — otherwise leftover tool traces can keep every score at 100.'
                  : 'Expand when you have tool traces, final-state data, voice/group-call artifacts, or a full vCon record.'}
              </p>
              {view === 'score' ? (
                <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '0 0 12px' }}>
                  <input
                    type="checkbox"
                    aria-label="Include structured evidence in Evaluate"
                    checked={includeStructuredEvidence}
                    onChange={(event) => onToggleStructuredEvidence(event.target.checked)}
                    style={{ marginTop: 3 }}
                  />
                  <span style={{ fontSize: 14, lineHeight: 1.4 }}>
                    Include structured evidence when evaluating (measures Task completion and Final state from action/tool trace and final state below). If those fields are empty, the scenario sample traces are filled in.
                  </span>
                </label>
              ) : null}
              <div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
                  <label style={{ display: 'grid', gap: 8 }}>
                    <span style={{ fontWeight: 700 }}>Action/tool trace</span>
                    <textarea value={actionTrace} onChange={(event) => setActionTrace(event.target.value)} rows={7} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }} />
                  </label>
                  <label style={{ display: 'grid', gap: 8 }}>
                    <span style={{ fontWeight: 700 }}>Final observed state</span>
                    <textarea value={finalState} onChange={(event) => setFinalState(event.target.value)} rows={7} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }} />
                  </label>
                </div>
                <label style={{ display: 'grid', gap: 8 }}>
                  <span style={{ fontWeight: 700 }}>Voice call evidence</span>
                  <textarea value={callEvidence} onChange={(event) => setCallEvidence(event.target.value)} rows={7} placeholder='{"turns":[{"speaker":"Caller","body":"I need a human."},{"speaker":"Agent","body":"I created a ticket and escalated you."}],"metrics":{"durationMs":92000,"avgLatencyMs":340}}' style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }} />
                </label>
                <label style={{ display: 'grid', gap: 8 }}>
                  <span style={{ fontWeight: 700 }}>Group call evidence</span>
                  <textarea value={groupCall} onChange={(event) => setGroupCall(event.target.value)} rows={7} placeholder='{"messages":[{"speaker":"Patient","text":"I need a refill"}],"decisions":["Route to clinician review"]}' style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }} />
                </label>
                <label style={{ display: 'grid', gap: 8 }}>
                  <span style={{ fontWeight: 700 }}>vCon record</span>
                  <textarea value={vconEvidence} onChange={(event) => setVconEvidence(event.target.value)} rows={7} placeholder='{"vcon":"0.0.1","parties":[{"name":"Caller"},{"name":"Agent"}],"dialog":[{"party":0,"body":"I need a human."}]}' style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }} />
                </label>
              </div>
            </details>
          </div>
        </details>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          {view !== 'score' ? <button
            type="button"
            disabled={isSimulating || isRunning || !selectedScenario}
            onClick={onSimulate}
            style={{
              border: '1px solid var(--border)',
              borderRadius: 8,
              background: 'white',
              color: 'var(--text)',
              padding: '12px 18px',
              fontWeight: 800,
              opacity: isSimulating || isRunning || !selectedScenario ? 0.65 : 1,
            }}
          >
            {isSimulating ? 'Simulating scenario...' : 'Simulate scenario'}
          </button> : null}
          {view !== 'score' ? <button
            type="button"
            disabled={isSimulating || isRunning || isEnqueueingSuite || !selectedSuite?.scenarios.length}
            onClick={onSimulateSuite}
            style={{
              border: '1px solid var(--border)',
              borderRadius: 8,
              background: 'white',
              color: 'var(--text)',
              padding: '12px 18px',
              fontWeight: 800,
              opacity: isSimulating || isRunning || isEnqueueingSuite || !selectedSuite?.scenarios.length ? 0.65 : 1,
            }}
          >
            {isSimulating ? 'Simulating suite...' : 'Simulate suite'}
          </button> : null}
          {view !== 'score' ? <button
            type="button"
            disabled={isSimulating || isRunning || isEnqueueingSuite || !selectedSuite?.scenarios.length}
            onClick={onEnqueueSuiteSimulation}
            style={{
              border: '1px solid var(--border)',
              borderRadius: 8,
              background: 'white',
              color: 'var(--text)',
              padding: '12px 18px',
              fontWeight: 800,
              opacity: isSimulating || isRunning || isEnqueueingSuite || !selectedSuite?.scenarios.length ? 0.65 : 1,
            }}
          >
            {isEnqueueingSuite ? 'Queueing simulated suite...' : 'Queue simulated suite'}
          </button> : null}
          {view !== 'simulate' ? <button
            type="submit"
            disabled={isRunning || isSimulating || isEnqueueingSuite || !selectedScenario || !hasRunnableEvidence}
            style={{
              border: 0,
              borderRadius: 8,
              background: 'var(--accent)',
              color: 'white',
              padding: '12px 18px',
              fontWeight: 800,
              opacity: isRunning || isSimulating || isEnqueueingSuite || !selectedScenario || !hasRunnableEvidence ? 0.65 : 1,
            }}
          >
            {isRunning ? 'Evaluating evidence...' : 'Evaluate evidence'}
          </button> : null}
          {view !== 'simulate' ? <button
            type="button"
            disabled={!report}
            onClick={onSaveRun}
            style={{
              border: '1px solid var(--border)',
              borderRadius: 8,
              background: userId ? 'white' : 'var(--panel-alt)',
              color: 'var(--text)',
              padding: '12px 18px',
              fontWeight: 800,
              opacity: report ? 1 : 0.65,
            }}
          >
            Save run
          </button> : null}
          {view !== 'simulate' ? (
            <div
              aria-label="LLM judge controls"
              style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}
            >
              <button
                type="button"
                disabled={!report || !judgeProviderReady || isJudging}
                onClick={() => void onJudge()}
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: 8,
                  background: judgeProviderReady ? 'white' : 'var(--panel-alt)',
                  color: 'var(--text)',
                  padding: '12px 18px',
                  fontWeight: 800,
                  opacity: report && judgeProviderReady && !isJudging ? 1 : 0.65,
                }}
              >
                {isJudging ? 'Requesting LLM judge…' : 'Request LLM judge'}
              </button>
              {openaiProvider?.status === 'connected' ? (
                <span style={{ color: 'var(--muted)', fontSize: 12, lineHeight: 1.3 }}>
                  {openaiProvider.email || 'OpenAI Codex'}
                  {openaiProvider.plan_type ? ` · ${openaiProvider.plan_type}` : ''}
                  {' · '}
                  <button
                    type="button"
                    onClick={() => void onDisconnectOpenAI()}
                    style={{
                      border: 0,
                      padding: 0,
                      background: 'transparent',
                      color: 'var(--muted)',
                      fontSize: 12,
                      textDecoration: 'underline',
                      cursor: 'pointer',
                    }}
                  >
                    Disconnect
                  </button>
                </span>
              ) : productConfig?.llm_judge_status === 'enabled' ? (
                <span style={{ color: 'var(--muted)', fontSize: 12, lineHeight: 1.3 }}>
                  API key judge ready
                </span>
              ) : (
                <button
                  type="button"
                  className="secondary-link"
                  disabled={isConnectingOpenAI}
                  onClick={() => void onConnectOpenAI()}
                  style={{ padding: '6px 10px', fontSize: 13, fontWeight: 600 }}
                >
                  {isConnectingOpenAI ? 'Connecting…' : 'Connect OpenAI'}
                </button>
              )}
            </div>
          ) : null}
        </div>

        {view === 'score' && openaiProviderMessage ? (
          <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>{openaiProviderMessage}</p>
        ) : null}

        {runError ? <p style={{ color: 'var(--error-text)', margin: 0 }}>{runError}</p> : null}
        {saveMessage ? <p style={{ color: 'var(--muted)', margin: 0 }}>{saveMessage}</p> : null}
        {judgeGate ? (
          <div
            aria-label="LLM judge result"
            style={{
              border: `1px solid ${judgeGate.status === 'ready' ? 'var(--success-border)' : 'var(--error-border)'}`,
              background: judgeGate.status === 'ready' ? 'var(--success-bg)' : 'var(--error-bg)',
              color: judgeGate.status === 'ready' ? 'var(--success-text)' : 'var(--error-text)',
              borderRadius: 8,
              padding: 12,
              display: 'grid',
              gap: 8,
            }}
          >
            <div>
              <strong>{judgeBannerTitle(judgeGate)}:</strong> {judgeGate.message}
            </div>
            {(judgeGate.provider || judgeGate.model || judgeGate.latency_ms != null) ? (
              <p style={{ margin: 0, color: 'inherit', fontSize: 13 }}>
                {[
                  judgeGate.provider ? `Provider: ${judgeGate.provider}` : null,
                  judgeGate.model ? `Model: ${judgeGate.model}` : null,
                  judgeGate.latency_ms != null ? `${judgeGate.latency_ms} ms` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            ) : null}
            {judgeGate.judge_result?.rationale ? (
              <p style={{ margin: 0, color: 'inherit' }}>
                <strong>Rationale:</strong> {judgeGate.judge_result.rationale}
              </p>
            ) : null}
            {judgeGate.judge_result?.next_action ? (
              <p style={{ margin: 0, color: 'inherit' }}>
                <strong>Next action:</strong> {judgeGate.judge_result.next_action}
              </p>
            ) : null}
            {!judgeGate.judge_result?.rationale && judgeGate.judge_output ? (
              <p style={{ margin: 0, color: 'inherit', whiteSpace: 'pre-wrap' }}>{judgeGate.judge_output}</p>
            ) : null}
            {formatJudgeSpend(judgeGate.spend_control) ? (
              <p style={{ margin: 0, color: 'inherit', fontSize: 13 }}>{formatJudgeSpend(judgeGate.spend_control)}</p>
            ) : null}
            {judgeGate.evidence_citations.length ? (
              <div>
                <p style={{ margin: '0 0 4px', fontSize: 13, fontWeight: 700 }}>Citations sent to judge</p>
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {judgeGate.evidence_citations.map((citation) => (
                    <li key={citation} style={{ fontSize: 13 }}>{citation}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {judgeGate.prompt_preview ? (
              <div>
                <button
                  type="button"
                  onClick={() => setShowJudgePrompt((current) => !current)}
                  style={{
                    border: 0,
                    padding: 0,
                    background: 'transparent',
                    color: 'inherit',
                    textDecoration: 'underline',
                    cursor: 'pointer',
                    fontSize: 13,
                    fontWeight: 700,
                  }}
                >
                  {showJudgePrompt ? 'Hide what the judge saw' : 'What the judge saw'}
                </button>
                {showJudgePrompt ? (
                  <pre
                    style={{
                      margin: '8px 0 0',
                      padding: 10,
                      borderRadius: 6,
                      background: 'rgba(0,0,0,0.06)',
                      color: 'inherit',
                      whiteSpace: 'pre-wrap',
                      fontSize: 12,
                      maxHeight: 280,
                      overflow: 'auto',
                    }}
                  >
                    {judgeGate.prompt_preview}
                  </pre>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </form>
      ) : null}

      <section id="launch-agent" className="card run-launch-card" style={{ display: view === 'score' || view === 'simulate' ? 'none' : 'grid' }} aria-label="Launch agent run">
        <div className="run-launch-heading">
          <div>
            <p className="eyebrow" style={{ margin: '0 0 6px' }}>
              Execute
            </p>
            <h2>Configure this run</h2>
            <p>
              Choose what to test and who drives the scenario. We will capture the transcript, evidence, and score in one run record.
            </p>
          </div>
        </div>

        {view === 'run' && loadError ? (
          <div style={{ border: '1px solid var(--error-border)', background: 'var(--error-bg)', color: 'var(--error-text)', borderRadius: 8, padding: 12, display: 'grid', gap: 8 }}>
            <span>{loadError}</span>
            <button type="button" className="secondary-link" onClick={() => setCatalogReloadKey((value) => value + 1)}>
              Retry loading suites
            </button>
          </div>
        ) : null}

        {view === 'run' && selectedSuite && !loadError ? (
          <div className="run-scenario-context" aria-label="Selected run scope">
            <div>
              <span>{executionScope === 'suite' && supportsSuiteExecutionScope ? 'Suite' : 'Scenario'}</span>
              <strong>
                {executionScope === 'suite' && supportsSuiteExecutionScope
                  ? selectedSuite.title
                  : selectedScenario?.title || 'Select a scenario'}
              </strong>
              <small>
                {executionScope === 'suite' && supportsSuiteExecutionScope
                  ? `${selectedSuite.scenarios.length} ${selectedSuite.scenarios.length === 1 ? 'scenario' : 'scenarios'}`
                  : selectedSuite.title}
              </small>
            </div>
            <ApiAwareLink href="/scenarios">
              {executionScope === 'suite' && supportsSuiteExecutionScope ? 'Review scenarios' : 'Change scenario'}
            </ApiAwareLink>
          </div>
        ) : null}

        {supportsSuiteExecutionScope ? (
          <fieldset className="run-scope-control">
            <legend>Run scope</legend>
            <div className="run-scope-toggle" role="group" aria-label="Run scope">
              <button
                type="button"
                className={executionScope === 'selected' ? 'is-active' : ''}
                aria-pressed={executionScope === 'selected'}
                onClick={() => setExecutionScope('selected')}
              >
                <strong>Single scenario</strong>
                <span>{selectedScenario?.title || 'Selected scenario'}</span>
              </button>
              <button
                type="button"
                className={executionScope === 'suite' ? 'is-active' : ''}
                aria-pressed={executionScope === 'suite'}
                onClick={() => setExecutionScope('suite')}
              >
                <strong>Entire suite</strong>
                <span>{selectedSuite?.scenarios.length || 0} scenarios</span>
              </button>
            </div>
          </fieldset>
        ) : null}

        {matchingProductProjects.length > 1 ? (
          <label style={{ display: 'grid', gap: 8, maxWidth: 420 }}>
            <span style={{ fontWeight: 700 }}>Project for this run</span>
            <select
              aria-label="Execution project"
              value={productProjectId}
              onChange={(event) => {
                const next = event.target.value;
                setProductProjectId(next);
                if (next) window.localStorage.setItem('conversation-evals-demo-product-project-id', next);
                else window.localStorage.removeItem('conversation-evals-demo-product-project-id');
              }}
            >
              <option value="">Select personal or workspace project</option>
              {matchingProductProjects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name} · {project.workspace_id ? 'workspace' : 'personal'}
                </option>
              ))}
            </select>
            <span style={{ color: 'var(--muted)', fontSize: 13 }}>
              Multiple visible projects use the key {projectId}; this keeps run history and ASSERT audits attached correctly.
            </span>
          </label>
        ) : null}

        <div className="run-config-grid">
          <div className="run-config-step">
            <div className="run-config-step-heading">
              <span>1</span>
              <div><strong>Tester</strong><small>Scenario driver</small></div>
            </div>
            <div className="run-tester-summary" aria-label="Execution tester">
              <span>Tester type</span>
              <strong>Scenario user (AI)</strong>
            </div>
            <p>
              Acts as the user defined by the selected scenario and adapts to the target&apos;s responses.
              The target, not the tester, is evaluated.
            </p>
          </div>
          <div className="run-config-step">
            <div className="run-config-step-heading">
              <span>2</span>
              <div><strong>Agent target</strong><small>System under test</small></div>
            </div>
            <label>
              <span className="sr-only">Agent target</span>
            <select
              aria-label="Execution agent target"
              value={selectedAgentId}
              onChange={(event) => {
                const agentId = event.target.value;
                setSelectedAgentId(agentId);
                const agent = agents.find((item) => item.id === agentId);
                if (!agent) return;
                if (agent.target === 'builtin_sample_voice') {
                  setExecutionMode('pipecat_webrtc');
                  setExecutionTesterId('pipecat_tester');
                  setExecutionExecutorId('cae_local_audio_loop');
                } else if (agent.target === 'pipecat_public_demo') {
                  setExecutionMode('pipecat_webrtc');
                  setExecutionTesterId('pipecat_tester');
                  setExecutionExecutorId('pipecat_public_daily');
                } else if (agent.target === 'signalwire_holy_guacamole') {
                  setExecutionMode('pipecat_webrtc');
                  setExecutionTesterId('pipecat_tester');
                  setExecutionExecutorId('signalwire_public_webrtc');
                  setExecutionMaxExchanges(1);
                } else if (agent.target === 'voice_fixture') {
                  setExecutionMode('voice_fixture');
                  setExecutionTesterId('fixture_replay');
                  setExecutionExecutorId('evidence_replay');
                } else if (agent.target === 'offline_acc_fixture') {
                  setExecutionMode('text_callable');
                  setExecutionTesterId('fixture_replay');
                  setExecutionExecutorId('evidence_replay');
                } else if (isExternalVoiceTargetId(agent.target)) {
                  setExecutionMode('text_callable');
                  setExecutionTesterId('scenario_simulator');
                  setExecutionExecutorId(
                    agent.target === 'sip_agent'
                      ? 'acc_sip'
                      : agent.target === 'phone_agent'
                        ? 'acc_phone'
                        : 'acc_browser_webrtc',
                  );
                } else {
                  setExecutionMode('text_callable');
                  setExecutionTesterId('scenario_simulator');
                  setExecutionExecutorId('local_async_runner');
                }
              }}
            >
              {!agents.length ? <option value="">No targets</option> : null}
              {agents.map((agent) => (
                <option key={agent.id} value={agent.id}>{agent.name}</option>
              ))}
            </select>
            </label>
            <p>{selectedScoreAgent ? (selectedScoreAgent.target === 'builtin_sample_voice' ? 'Built-in reference agent' : isFixtureTargetId(selectedScoreAgent.target) ? 'Built-in sample agent' : 'Live target') : 'Choose a configured target'}</p>
          </div>
          <div className="run-config-step">
            <div className="run-config-step-heading">
              <span>3</span>
              <div><strong>Execution</strong><small>{executionExecutorId === 'pipecat_public_daily' ? 'Direct Daily WebRTC' : executionExecutorId === 'signalwire_public_webrtc' ? 'Direct SignalWire WebRTC' : 'Local runner'}</small></div>
            </div>
            <div className="run-execution-fields" aria-label="Execution runner">
              <div><span>Executor</span><strong>{executionExecutorId.replaceAll('_', ' ')}</strong></div>
              <label>
                <span>Iterations</span>
                <input
                  aria-label="Execution iterations"
                  type="number"
                  min={1}
                  max={20}
                  value={executionIterations}
                  onChange={(event) => setExecutionIterations(Math.max(1, Math.min(20, Number(event.target.value) || 1)))}
                />
              </label>
              <label>
                <span>Max exchanges</span>
                <input
                  aria-label="Maximum exchanges"
                  type="number"
                  min={1}
                  max={selectedScoreAgent?.target === 'signalwire_holy_guacamole' ? 2 : 10}
                  value={executionMaxExchanges}
                  aria-invalid={executionMaxExchanges === ''}
                  disabled={!selectedScoreAgent || (
                     selectedScoreAgent.target !== 'openai_codex'
                     && selectedScoreAgent.target !== 'builtin_sample_voice'
                     && selectedScoreAgent.target !== 'pipecat_public_demo'
                     && selectedScoreAgent.target !== 'signalwire_holy_guacamole'
                  )}
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    setExecutionMaxExchanges(
                      nextValue === ''
                        ? ''
                        : Math.max(
                          1,
                          Math.min(
                            selectedScoreAgent?.target === 'signalwire_holy_guacamole' ? 2 : 10,
                            Number(nextValue),
                          ),
                        ),
                    );
                  }}
                />
              </label>
              {selectedScoreAgent?.target === 'builtin_sample_voice'
              || selectedScoreAgent?.target === 'pipecat_public_demo'
              || selectedScoreAgent?.target === 'signalwire_holy_guacamole' ? (
                <label>
                  <span>Session timeout (seconds)</span>
                  <input
                    aria-label="Duplex session timeout"
                    type="number"
                    min={30}
                    max={300}
                    value={executionDuplexTimeoutSeconds}
                    onChange={(event) => setExecutionDuplexTimeoutSeconds(
                      Math.max(30, Math.min(300, Number(event.target.value) || 120)),
                    )}
                  />
                </label>
              ) : null}
            </div>
            <p>
              Queues the run and writes the ASSERT inference set locally.
              {selectedScoreAgent?.target === 'openai_codex'
              || selectedScoreAgent?.target === 'builtin_sample_voice'
              || selectedScoreAgent?.target === 'pipecat_public_demo'
                ? ' One exchange is one tester message plus one agent response.'
                : selectedScoreAgent?.target === 'signalwire_holy_guacamole'
                  ? ' One exchange is one tester message plus one remote target response; SignalWire supports up to two in the same WebRTC call.'
                : ' Choose a generalist text or voice agent to configure exchanges; fixed sample targets replay one exchange.'}
            </p>
          </div>
        </div>

        {selectedScoreAgent?.target === 'openai_codex' || selectedScoreAgent?.target === 'builtin_sample_voice' ? (
          <label style={{ display: 'grid', gap: 8, maxWidth: 360 }}>
            <span style={{ fontWeight: 700 }}>Target model</span>
            <select
              aria-label="Execution model"
              value={executionModelName}
              onChange={(event) => setExecutionModelName(event.target.value)}
              style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px' }}
            >
              {(executionModelOptions.includes(executionModelName)
                ? executionModelOptions
                : [executionModelName, ...executionModelOptions]
              ).map((modelId) => (
                <option key={modelId} value={modelId}>{modelId}</option>
              ))}
            </select>
            {openaiProvider?.status !== 'connected' ? (
              <span style={{ color: 'var(--muted)', fontSize: 13 }}>
                {selectedScoreAgent.id === 'generalist-text-agent' || selectedScoreAgent.target === 'builtin_sample_voice'
                  ? 'This reference target can use OPENAI_API_KEY, a connected OpenAI account, or local Ollama for ollama/... model ids. '
                  : 'Connect OpenAI to run this target. '}
                <button type="button" className="secondary-link" disabled={isConnectingOpenAI} onClick={() => void onConnectOpenAI()} style={{ padding: 0, border: 0, background: 'transparent', color: 'var(--accent)', fontWeight: 700, cursor: 'pointer' }}>
                  {isConnectingOpenAI ? 'Connecting…' : 'Connect OpenAI'}
                </button>
              </span>
            ) : executionModelsMessage ? <span style={{ color: 'var(--muted)', fontSize: 13 }}>{executionModelsMessage}</span> : null}
          </label>
        ) : null}

        {selectedScoreAgent ? (
          <div className={`run-provenance-notice ${isFixtureTargetId(selectedScoreAgent.target) || isSavedReplayTargetId(selectedScoreAgent.target) ? 'is-fixture' : 'is-live'}`} role="note">
            <strong>
              {isSavedReplayTargetId(selectedScoreAgent.target)
                ? 'Saved evidence replay'
                : selectedScoreAgent.target === 'builtin_sample_voice'
                  ? 'Built-in generalist reference agent'
                : selectedScoreAgent.target === 'pipecat_public_demo'
                  ? 'Public Pipecat target'
                : selectedScoreAgent.target === 'signalwire_holy_guacamole'
                  ? 'Holy Guacamole SignalWire target'
                : isFixtureTargetId(selectedScoreAgent.target)
                  ? 'Built-in sample agent'
                  : 'Live target'}
            </strong>
            <span>
              {selectedScoreAgent.target === 'http_endpoint'
                ? `POSTs to ${selectedScoreAgent.connection?.endpoint_url || 'the configured endpoint'}; black-box response evidence only.`
                : selectedScoreAgent.target === 'builtin_sample_voice'
                  ? 'Runs a separate Pipecat target through rtc-asr, the configured LLM, and Kokoro. Evaluation uses current-run local evidence; it is not a browser, SIP, or phone call.'
                  : selectedScoreAgent.target === 'pipecat_public_demo'
                    ? 'Runs the selected scenario through direct Daily WebRTC and captures current-run audio, transcript, latency, evaluation, and vCon evidence without a browser.'
                   : selectedScoreAgent.target === 'signalwire_holy_guacamole'
                     ? 'Runs up to two exchanges through one direct public SignalWire WebRTC call and captures current-run audio, latency, recording evidence, and vCon media without launching a browser.'
                  : isSavedReplayTargetId(selectedScoreAgent.target)
                    ? 'Uses saved evidence. Replay is not a live agent destination.'
                    : isExternalVoiceTargetId(selectedScoreAgent.target)
                      ? 'ACC owns this live destination, but the CAE launch and evidence adapter is not implemented yet.'
                      : isFixtureTargetId(selectedScoreAgent.target)
                        ? 'Uses predictable sample responses and does not contact a deployed agent.'
                  : 'Invokes the connected provider and records the returned response.'}
            </span>
          </div>
        ) : null}

        {selectedScoreAgent?.target === 'builtin_sample_voice' && !referenceVoicePreflight?.ready ? (
          <div className="voice-error" role="alert" aria-label="Run Agent voice preflight blocked">
            <strong>Voice run blocked before queueing</strong>
            {referenceVoicePreflight ? (
              <ul className="voice-preflight-list">
                {referenceVoicePreflight.dependencies.filter((item) => !item.ready).map((item) => (
                  <li key={item.id}>
                    <strong>{item.label}:</strong> {item.detail}{' '}
                    {item.setup_url ? (
                      <a href={item.setup_url} target="_blank" rel="noreferrer" aria-label={`${item.label} setup`}>
                        Setup guide ↗
                      </a>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : <span>{referenceVoicePreflightError || 'Checking OpenAI, shared token, Pipecat, rtc-asr, and Kokoro reachability.'}</span>}
          </div>
        ) : null}
        <div className="run-launch-actions">
          <div>
            <strong>{selectedScoreAgent ? `Ready to test ${selectedScoreAgent.name}` : 'Choose a target to continue'}</strong>
            <span>
              {executionScope === 'suite' && supportsSuiteExecutionScope
                ? `${selectedSuite?.scenarios.length || 0} scenarios × ${executionIterations} ${executionIterations === 1 ? 'iteration' : 'iterations'} · ${(selectedSuite?.scenarios.length || 0) * executionIterations} conversations`
                : `${executionIterations} ${executionIterations === 1 ? 'conversation' : 'conversations'}`}
              {selectedScoreAgent?.target === 'openai_codex'
              || selectedScoreAgent?.target === 'builtin_sample_voice'
              || selectedScoreAgent?.target === 'pipecat_public_demo'
              || selectedScoreAgent?.target === 'signalwire_holy_guacamole'
                ? executionMaxExchanges === ''
                  ? ' · enter an exchange cap'
                  : ` · up to ${executionMaxExchanges} ${executionMaxExchanges === 1 ? 'exchange' : 'exchanges'} each`
                : ''}
              {' · results appear below'}
            </span>
          </div>
          <button
            type="button"
            disabled={
              isLaunchingExecution
              || isRunning
              || isSimulating
              || !selectedSuite
              || !selectedScoreAgent
              || (matchingProductProjects.length > 1 && !productProjectId)
              || ((selectedScoreAgent?.target === 'openai_codex'
                || selectedScoreAgent?.target === 'builtin_sample_voice'
                || selectedScoreAgent?.target === 'pipecat_public_demo'
                || selectedScoreAgent?.target === 'signalwire_holy_guacamole')
                && executionMaxExchanges === '')
              || (selectedScoreAgent.target === 'openai_codex'
                && selectedScoreAgent.id !== 'generalist-text-agent'
                && openaiProvider?.status !== 'connected')
              || isExternalVoiceTargetId(selectedScoreAgent.target)
              || (selectedScoreAgent.target === 'builtin_sample_voice' && !referenceVoicePreflight?.ready)
            }
            onClick={() => void onLaunchExecution()}
          >
            {isLaunchingExecution
              ? 'Starting evaluation...'
              : executionRun && isActiveExecutionStatus(executionRun.status)
                ? 'Evaluation running...'
                : 'Run evaluation'}
          </button>
        </div>

        {executionMessage ? <p style={{ margin: 0, color: 'var(--muted)' }}>{executionMessage}</p> : null}

        {executionRun ? (
          <div style={{ display: 'grid', gap: 12 }}>
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 12,
                justifyContent: 'space-between',
                alignItems: 'center',
                borderTop: '1px solid var(--border)',
                paddingTop: 12,
              }}
            >
              <div>
                <strong>{executionRun.execution_run_id}</strong>
                <span style={{ marginLeft: 10, color: executionStatusColor(executionRun.status), fontWeight: 800, textTransform: 'capitalize' }}>
                  {executionRun.status}
                </span>
                <a href={executionAnalysisHref(executionRun.execution_run_id)} style={{ marginLeft: 12, fontWeight: 760 }}>
                  Open analysis
                </a>
              </div>
              <div style={{ color: 'var(--muted)', fontSize: 14 }}>
                {executionRun.progress?.completed_conversations ?? 0}/{executionRun.progress?.total_conversations ?? 0} conversations ·{' '}
                {executionRun.progress?.percent ?? 0}%
                {executionRun.inference_set_path ? ` · ${executionRun.inference_set_path}` : ''}
              </div>
            </div>

            <LiveRunFeedback
              conversations={executionRun.conversations || []}
              apiBase={getApiBase()}
              voice={executionRun.mode === 'pipecat_webrtc'}
              executionRunId={executionRun.execution_run_id}
              userId={userId}
              runStatus={executionRun.status}
            />

            <div aria-label="Execution conversations" style={{ display: 'grid', gap: 8, maxHeight: 420, overflow: 'auto' }}>
              {(executionRun.conversations || []).length ? (
                [...executionRun.conversations].reverse().map((conversation) => {
                  const turnCount = conversation.turns?.length ?? 0;
                  const latencyCount = conversation.latency_marks?.length ?? 0;
                  const recordingSummary = executionRecordingSummary(conversation.recording);
                  const vconSummary = executionVconSummary(
                    conversation.vcon_export_summary,
                    conversation.vcon_export,
                  );
                  const audioSessionSummary = executionAudioSessionSummary(conversation.audio_session);
                  return (
                    <article
                      key={conversation.conversation_id}
                      style={{
                        border: '1px solid var(--border)',
                        borderRadius: 10,
                        padding: '12px 14px',
                        background: conversation.status === 'running' ? 'var(--panel-alt)' : 'white',
                        display: 'grid',
                        gap: 6,
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                        <div>
                          <strong>{conversation.scenario_title || conversation.scenario_id}</strong>
                          <span style={{ marginLeft: 8, color: 'var(--muted)', fontSize: 13 }}>
                            iter {conversation.iteration ?? 1} · {conversation.mode}
                          </span>
                        </div>
                        <span style={{ color: executionStatusColor(conversation.status), fontWeight: 800, textTransform: 'capitalize' }}>
                          {conversation.status}
                        </span>
                      </div>
                      <div style={{ color: 'var(--muted)', fontSize: 13, display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                        <span>{turnCount} turns</span>
                        {latencyCount ? <span>{latencyCount} latency marks</span> : null}
                        {conversation.verdict ? (
                          <span style={{ color: executionStatusColor(conversation.verdict), textTransform: 'capitalize' }}>
                            {conversation.verdict}
                            {typeof conversation.score === 'number' ? ` · ${conversation.score}` : ''}
                            {conversation.mode === 'pipecat_webrtc' ? ' · current-run duplex evidence' : ''}
                          </span>
                        ) : null}
                        {conversation.error ? <span style={{ color: 'var(--error-text)' }}>{conversation.error}</span> : null}
                      </div>
                      {recordingSummary ? (
                        <p style={{ margin: 0, fontSize: 13, color: 'var(--text)' }}>
                          <strong>Recording:</strong> {recordingSummary}
                        </p>
                      ) : null}
                      {vconSummary ? (
                        <p style={{ margin: 0, fontSize: 13, color: 'var(--text)' }}>
                          <strong>vCon:</strong> {vconSummary}
                        </p>
                      ) : null}
                      {audioSessionSummary ? (
                        <p style={{ margin: 0, fontSize: 13, color: 'var(--muted)' }}>
                          <strong>Audio session:</strong> {audioSessionSummary}
                        </p>
                      ) : null}
                      {conversation.transcript ? (
                        <p style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 13, color: 'var(--text)', maxHeight: 72, overflow: 'hidden' }}>
                          {conversation.transcript}
                        </p>
                      ) : null}
                    </article>
                  );
                })
              ) : (
                <p style={{ margin: 0, color: 'var(--muted)' }}>Waiting for the first conversation row…</p>
              )}
            </div>
          </div>
        ) : null}
      </section>

      {suiteSimulation && view !== 'score' && view !== 'run' ? (
        <section className="card" style={{ padding: 24, display: 'grid', gap: 16 }} aria-label="Suite simulation summary">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
            <div>
              <p style={{ margin: '0 0 6px', color: 'var(--muted)' }}>Suite simulation</p>
              <h2 style={{ margin: 0, fontSize: 26 }}>{suiteSimulation.suite_name ?? selectedSuite?.title ?? suiteSimulation.suite_id}</h2>
            </div>
            <div style={{ textAlign: 'right' }}>
              <strong style={{ display: 'block', fontSize: 28, color: scoreColor(suiteSimulation.average_score) }}>{suiteSimulation.average_score}</strong>
              <span style={{ color: 'var(--muted)', textTransform: 'capitalize' }}>{suiteSimulation.verdict}</span>
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
            <ScoreTile label="Scenarios" score={suiteSimulation.scenario_count} />
            <ScoreTile label="Passing" score={suiteSimulation.pass_count} />
            <ScoreTile label="Needs review" score={suiteSimulation.needs_review_count} />
          </div>
          <section style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, display: 'grid', gap: 12, background: 'var(--panel-alt)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <div>
                <h3 style={{ margin: 0 }}>Suite brief</h3>
                <p style={{ margin: '4px 0 0', color: 'var(--muted)' }}>Aggregate summary for regression handoff and release notes.</p>
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <button
                  type="button"
                  onClick={onSaveSuiteRuns}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    background: userId ? 'white' : 'var(--panel-alt)',
                    color: 'var(--text)',
                    padding: '10px 14px',
                    fontWeight: 800,
                  }}
                >
                  Save suite runs
                </button>
                <button
                  type="button"
                  onClick={onExportSuiteVconBundle}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    background: 'white',
                    color: 'var(--text)',
                    padding: '10px 14px',
                    fontWeight: 800,
                  }}
                >
                  Export suite vCon bundle
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      await copyText(suiteBrief);
                      setCopyMessage('Copied suite brief.');
                    } catch {
                      setCopyMessage('Could not copy suite brief.');
                    }
                  }}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    background: 'white',
                    color: 'var(--text)',
                    padding: '10px 14px',
                    fontWeight: 800,
                  }}
                >
                  Copy suite brief
                </button>
              </div>
            </div>
            <pre
              aria-label="Suite brief"
              style={{
                margin: 0,
                whiteSpace: 'pre-wrap',
                overflowWrap: 'anywhere',
                background: 'white',
                border: '1px solid var(--border)',
                borderRadius: 8,
                padding: 14,
                lineHeight: 1.5,
              }}
            >
              {suiteBrief}
            </pre>
          </section>
          <div style={{ display: 'grid', gap: 8 }}>
            {suiteSimulation.scenario_runs.map((run) => {
              const scenarioId = run.scenario_id ?? run.benchmark_report.scenario_id ?? '';
              return (
                <button
                  type="button"
                  key={run.benchmark_report.run_id ?? scenarioId}
                  onClick={() => {
                    setSelectedScenarioId(scenarioId);
                    setTranscript(run.transcript);
                    setActionTrace(stringifyEditable(run.action_trace, '[]'));
                    setFinalState(stringifyEditable(run.final_state, '{}'));
                    setReport(run.benchmark_report);
                  }}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    background: scenarioId === report?.scenario_id ? 'var(--panel-alt)' : 'white',
                    color: 'var(--text)',
                    padding: 12,
                    display: 'flex',
                    justifyContent: 'space-between',
                    gap: 12,
                    textAlign: 'left',
                    fontWeight: 760,
                  }}
                >
                  <span>{run.scenario_title ?? run.benchmark_report.scenario_title ?? scenarioId}</span>
                  <span>{run.benchmark_report.verdict} / {run.benchmark_report.overall_score}</span>
                </button>
              );
            })}
          </div>
        </section>
      ) : null}


      {report ? (
        <section className="card" style={{ padding: 24, display: 'grid', gap: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
            <div>
              <p style={{ margin: '0 0 6px', color: 'var(--muted)' }}>Benchmark report</p>
              <h2 style={{ margin: 0, fontSize: 28, textTransform: 'capitalize' }}>{verdict ?? 'Complete'}</h2>
            </div>
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              <button
                type="button"
                onClick={onExportCurrentReport}
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: 8,
                  background: 'white',
                  color: 'var(--text)',
                  padding: '10px 14px',
                  fontWeight: 800,
                }}
              >
                Download report JSON
              </button>
              {score !== undefined ? (
                <div style={{ fontSize: 40, fontWeight: 900, color: scoreColor(score) }}>{score}</div>
              ) : null}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 12 }}>
            <ScoreTile label="Required actions" score={report.required_action_score} />
            {report.score_components && 'rubric' in report.score_components ? (
              <ScoreTile label="Rubric" score={report.rubric_score} />
            ) : null}
            <ScoreTile label="Forbidden actions" score={report.forbidden_action_score} />
            {typeof report.task_completion_score === 'number' ? (
              <ScoreTile label="Task completion" score={report.task_completion_score} />
            ) : null}
            {typeof report.final_state_score === 'number' ? (
              <ScoreTile label="Final state" score={report.final_state_score} />
            ) : null}
          </div>
          {report.scoring_mode === 'transcript' ? (
            <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13, lineHeight: 1.45 }} aria-label="Transcript scoring note">
              Transcript-only mode: Task completion and Final state are not shown (not measured — not counted as 100).
              Include structured evidence or load the full sample to measure them.
            </p>
          ) : null}
          {report.score_components && Object.keys(report.score_components).length ? (
            <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13, lineHeight: 1.45 }} aria-label="Score breakdown">
              Score mode: {report.scoring_mode || 'unknown'} ·{' '}
              {Object.entries(report.score_components)
                .map(([key, value]) => `${key.replace(/_/g, ' ')} ${value}`)
                .join(' · ')}
            </p>
          ) : null}

          {(report.completed_actions?.length || report.missing_actions?.length || selectedScenario?.required_actions?.length) ? (
            <section
              aria-label="Required action checklist"
              style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, display: 'grid', gap: 10, background: 'white' }}
            >
              <div>
                <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 13, fontWeight: 800, textTransform: 'uppercase' }}>
                  Required action checklist
                </p>
                <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>
                  Observed in this evidence vs still missing.
                </p>
              </div>
              <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none', display: 'grid', gap: 6 }}>
                {(selectedScenario?.required_actions?.length
                  ? toStringList(selectedScenario.required_actions)
                  : [...(report.completed_actions ?? []), ...(report.missing_actions ?? [])]
                ).map((action) => {
                  const completed = (report.completed_actions ?? []).some(
                    (item) => item.toLowerCase() === action.toLowerCase(),
                  );
                  const missing = (report.missing_actions ?? []).some(
                    (item) => item.toLowerCase() === action.toLowerCase(),
                  );
                  const status = completed ? 'observed' : missing || report.missing_actions?.length ? 'missing' : 'unknown';
                  return (
                    <li
                      key={action}
                      style={{
                        display: 'flex',
                        gap: 10,
                        alignItems: 'flex-start',
                        fontSize: 14,
                        lineHeight: 1.4,
                        color: status === 'missing' ? 'var(--error-text)' : 'var(--text)',
                      }}
                    >
                      <span aria-hidden="true" style={{ fontWeight: 900, minWidth: 16 }}>
                        {status === 'observed' ? '✓' : status === 'missing' ? '✗' : '·'}
                      </span>
                      <span>
                        <strong>{status === 'observed' ? 'Observed' : status === 'missing' ? 'Missing' : 'Unchecked'}:</strong> {action}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null}

          {actionPlan ? (
            <section
              aria-label="Operator action plan"
              style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, display: 'grid', gap: 12, background: 'white' }}
            >
              <div>
                <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 13, fontWeight: 800, textTransform: 'uppercase' }}>Operator action plan</p>
                <h3 style={{ margin: 0 }}>{actionPlan.headline}</h3>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10 }}>
                <AuditFact label="Primary risk" value={actionPlan.primaryRisk} />
                <AuditFact label="Next step" value={actionPlan.nextStep} />
                <AuditFact label="Regression note" value={actionPlan.regression} />
                {actionPlan.failureCategories ? (
                  <AuditFact label="Failure categories" value={actionPlan.failureCategories} />
                ) : null}
              </div>
              {actionPlan.headline === 'Keep moving through uncovered scenarios' && suiteScenarioCoverage?.recommended_next_scenario?.id ? (
                <div>
                  <button
                    type="button"
                    onClick={onSelectRecommendedScenario}
                    style={{
                      border: '1px solid var(--border)',
                      borderRadius: 999,
                      background: 'var(--surface)',
                      color: 'var(--text)',
                      padding: '8px 12px',
                      fontWeight: 800,
                    }}
                  >
                    Open {suiteScenarioCoverage.recommended_next_scenario.title ?? suiteScenarioCoverage.recommended_next_scenario.id}
                  </button>
                </div>
              ) : null}
            </section>
          ) : null}

          {currentRegressionDelta ? (
            <section
              aria-label="Unsaved regression comparison"
              style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, display: 'grid', gap: 8, background: 'var(--panel-alt)' }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                <h3 style={{ margin: 0, color: regressionDeltaColor(currentRegressionDelta.status) }}>
                  Current run: {currentRegressionDelta.status}
                </h3>
                <strong style={{ color: regressionDeltaColor(currentRegressionDelta.status) }}>
                  {formatSignedDelta(currentRegressionDelta.score_delta)}
                </strong>
              </div>
              <p style={{ margin: 0, color: 'var(--muted)' }}>
                {regressionDeltaSummary(currentRegressionDelta)}
                {currentRegressionDelta.previous_run_id ? ` against ${currentRegressionDelta.previous_run_id}` : ' before saving.'}
              </p>
            </section>
          ) : null}

          <section style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, display: 'grid', gap: 12, background: 'var(--panel-alt)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <div>
                <h3 style={{ margin: 0 }}>Report brief</h3>
                <p style={{ margin: '4px 0 0', color: 'var(--muted)' }}>Share-ready summary for handoff, tickets, and customer updates.</p>
              </div>
              <button
                type="button"
                onClick={async () => {
                  try {
                    await copyText(reportBrief);
                    setCopyMessage('Copied report brief.');
                  } catch {
                    setCopyMessage('Could not copy report brief.');
                  }
                }}
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: 8,
                  background: 'white',
                  color: 'var(--text)',
                  padding: '10px 14px',
                  fontWeight: 800,
                }}
              >
                Copy brief
              </button>
            </div>
            <pre
              aria-label="Report brief"
              style={{
                margin: 0,
                whiteSpace: 'pre-wrap',
                overflowWrap: 'anywhere',
                background: 'white',
                border: '1px solid var(--border)',
                borderRadius: 8,
                padding: 14,
                lineHeight: 1.5,
              }}
            >
              {reportBrief}
            </pre>
            {copyMessage ? <p style={{ margin: 0, color: 'var(--muted)' }}>{copyMessage}</p> : null}
          </section>

          <RunMetadataPanel metadata={report.run_metadata} />
          <EvidenceAuditPanel summary={report.evidence_audit_summary} />
          <section style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, display: 'grid', gap: 12, background: 'var(--panel-alt)' }}>
            <div>
              <h3 style={{ margin: 0 }}>Contract evidence</h3>
              <p style={{ margin: '4px 0 0', color: 'var(--muted)' }}>Immutable suite and scenario fingerprints attached to this result.</p>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
              <AuditFact label="Suite manifest" value={reportSuiteManifestFingerprint} />
              <AuditFact label="Scenario contract" value={scenarioContractFingerprint} />
              <AuditFact label="Required artifacts" value={(contractManifest?.evidence_requirements?.required_artifacts ?? []).join(', ') || 'Not declared'} />
              <AuditFact label="Optional artifacts" value={(contractManifest?.evidence_requirements?.optional_artifacts ?? []).join(', ') || 'None'} />
            </div>
          </section>
          <SimulationValidationPanel validation={report.simulation_validation} onRegenerate={onSimulate} isRegenerating={isSimulating} />
          <VoiceInteractionPanel summary={report.voice_interaction_summary} />
          <GroupCallPanel summary={report.group_call_summary} />

          {report.vcon_export ? (
            <section style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, display: 'grid', gap: 12, background: 'var(--panel-alt)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                <div>
                  <h3 style={{ margin: 0 }}>vCon export</h3>
                  <p style={{ margin: '4px 0 0', color: 'var(--muted)' }}>Portable conversation record with the benchmark analysis appended.</p>
                </div>
                <button
                  type="button"
                  onClick={onExportCurrentVcon}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    background: 'white',
                    color: 'var(--text)',
                    padding: '10px 14px',
                    fontWeight: 800,
                  }}
                >
                  Download vCon JSON
                </button>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
                <AuditFact label="Source" value={String(report.vcon_export.source_format ?? 'benchmark')} />
                <AuditFact label="Dialog turns" value={String(Array.isArray(report.vcon_export.dialog) ? report.vcon_export.dialog.length : 0)} />
                <AuditFact label="Contract" value={scenarioContractFingerprint} />
                <AuditFact label="Analysis" value={String(report.vcon_export.appended_analysis_type ?? 'agentic_benchmark_eval')} />
              </div>
            </section>
          ) : null}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
            <ReportList title="Failure categories" items={report.failure_categories} empty="No failure categories reported." />
            <ReportList title="Missing actions" items={report.missing_actions} empty="No missing required actions reported." />
            <ReportList title="Forbidden actions observed" items={report.forbidden_actions_observed} empty="No forbidden actions observed." />
            <ReportList title="Suggested fixes" items={report.suggested_fixes} empty="No suggested fixes reported." />
          </div>

          <div>
            <h3 style={{ marginTop: 0 }}>Evidence</h3>
            {evidence.length ? (
              <ul style={{ marginBottom: 0 }}>
                {evidence.map((item, index) => (
                  <EvidenceItem key={`${index}-${typeof item === 'string' ? item : JSON.stringify(item)}`} item={item} />
                ))}
              </ul>
            ) : (
              <p style={{ margin: 0, color: 'var(--muted)' }}>No evidence spans returned.</p>
            )}
          </div>

          <div>
            <h3 style={{ marginTop: 0 }}>Evidence citations</h3>
            {evidenceCitations.length ? (
              <ul style={{ marginBottom: 0 }}>
                {evidenceCitations.map((item, index) => (
                  <li key={`${index}-${typeof item === 'string' ? item : JSON.stringify(item)}`}>{formatCitationItem(item)}</li>
                ))}
              </ul>
            ) : (
              <p style={{ margin: 0, color: 'var(--muted)' }}>No evidence citations returned.</p>
            )}
          </div>

          <details>
            <summary style={{ cursor: 'pointer', fontWeight: 800 }}>Raw benchmark report</summary>
            <pre style={{ overflowX: 'auto', background: '#0f172a', color: '#e2e8f0', borderRadius: 8, padding: 16 }}>
              {JSON.stringify(report, null, 2)}
            </pre>
          </details>
        </section>
      ) : null}

      {view === 'all' || (view === 'score' && Boolean(report || savedRuns.length || suiteRuns.length)) ? (
      <section className="validation-grid" aria-label="Saved runs and e2e validation">
        <div className="card" style={{ padding: 20, display: 'grid', gap: 12 }}>
          <p className="eyebrow">Saved runs</p>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0 }}>
              {userId ? `${savedRuns.length} saved for ${selectedScenario?.title ?? projectId}` : 'Signup required'}
            </h3>
            {userId ? (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {savedRuns.length ? (
                  <button
                    type="button"
                    onClick={() => void onExportProjectHistory()}
                    style={{
                      border: '1px solid var(--border)',
                      borderRadius: 8,
                      background: 'white',
                      color: 'var(--text)',
                      padding: '8px 12px',
                      fontWeight: 800,
                    }}
                  >
                    Export history
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => void onExportBenchmarkRunHistory()}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    background: 'white',
                    color: 'var(--text)',
                    padding: '8px 12px',
                    fontWeight: 800,
                  }}
                >
                  Export benchmark history
                </button>
              </div>
            ) : null}
          </div>
          {projectRegressionSummary ? (
            <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'var(--panel-alt)', display: 'grid', gap: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <strong style={{ color: regressionDeltaColor(projectRegressionSummary.latest_status) }}>
                  Project trend: {projectRegressionSummary.latest_status}
                </strong>
                <span style={{ color: 'var(--muted)', fontWeight: 800 }}>
                  Avg {projectRegressionSummary.average_score ?? 'n/a'}
                </span>
              </div>
              <p style={{ margin: 0, color: 'var(--muted)' }}>
                Latest {projectRegressionSummary.latest_score ?? 'n/a'} vs previous {projectRegressionSummary.previous_score ?? 'n/a'}
                {' '}({formatSignedDelta(projectRegressionSummary.latest_delta)}), best {projectRegressionSummary.best_score ?? 'n/a'}.
              </p>
              <p style={{ margin: 0, color: 'var(--muted)' }}>
                Pass rate {projectRegressionSummary.pass_rate ?? 'n/a'}% across {projectRegressionSummary.run_count} runs
                {' '}({projectRegressionSummary.passing_runs ?? 0} pass, {projectRegressionSummary.failing_runs ?? 0} review).
              </p>
              {projectRegressionSummary.scenario_summaries?.length ? (
                <div style={{ display: 'grid', gap: 6 }}>
                  <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13, fontWeight: 800 }}>Scenario trends</p>
                  <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--muted)', display: 'grid', gap: 4 }}>
                    {projectRegressionSummary.scenario_summaries.slice(0, 3).map((summary) => (
                      <li key={`${summary.suite_id ?? 'suite'}:${summary.scenario_id}`}>
                        <span style={{ color: regressionDeltaColor(summary.latest_status), fontWeight: 800 }}>
                          {summary.latest_status}
                        </span>
                        {': '}
                        {scenarioSummaryLabel(summary)}
                        {' '}({summary.latest_score ?? 'n/a'} vs {summary.previous_score ?? 'n/a'}, {formatSignedDelta(summary.latest_delta)}; pass rate {summary.pass_rate ?? 'n/a'}%)
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {projectRegressionSummary.failure_category_summary?.length ? (
                <div style={{ display: 'grid', gap: 6 }}>
                  <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13, fontWeight: 800 }}>Failure mix</p>
                  <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--muted)', display: 'grid', gap: 4 }}>
                    {projectRegressionSummary.failure_category_summary.slice(0, 3).map((summary) => (
                      <li key={summary.category}>
                        <strong style={{ color: 'var(--danger)' }}>{summary.category}</strong>
                        {': '}{summary.count} run{summary.count === 1 ? '' : 's'}
                        {summary.latest_run_id ? `, latest ${summary.latest_run_id}` : ''}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
          {scenarioRegressionSummary ? (
            <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white', display: 'grid', gap: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <strong style={{ color: regressionDeltaColor(scenarioRegressionSummary.latest_status) }}>
                  Selected scenario: {scenarioRegressionSummary.latest_status}
                </strong>
                <span style={{ color: 'var(--muted)', fontWeight: 800 }}>
                  {scenarioRegressionSummary.run_count} focused runs
                </span>
              </div>
              <p style={{ margin: 0, color: 'var(--muted)' }}>
                Latest {scenarioRegressionSummary.latest_score ?? 'n/a'} vs previous {scenarioRegressionSummary.previous_score ?? 'n/a'}
                {' '}({formatSignedDelta(scenarioRegressionSummary.latest_delta)}), pass rate {scenarioRegressionSummary.pass_rate ?? 'n/a'}%.
              </p>
            </div>
          ) : null}
          {suiteScenarioCoverage ? (
            <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white', display: 'grid', gap: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <strong style={{ color: suiteScenarioCoverage.coverage_status === 'complete' ? 'var(--success-text)' : 'var(--text)' }}>
                  Suite scenario coverage
                </strong>
                <span style={{ color: 'var(--muted)', fontWeight: 800 }}>
                  {suiteScenarioCoverage.covered_scenario_count}/{suiteScenarioCoverage.scenario_count ?? 0} covered
                </span>
              </div>
              <p style={{ margin: 0, color: 'var(--muted)' }}>
                {scenarioCoverageExportSummary(suiteScenarioCoverage)}
              </p>
              {suiteScenarioCoverage.recommended_next_scenario?.title ? (
                <p style={{ margin: 0, color: 'var(--text)', fontSize: 13, fontWeight: 700 }}>
                  Recommended next: {suiteScenarioCoverage.recommended_next_scenario.title}.
                </p>
              ) : null}
              {suiteScenarioCoverage.recommended_next_scenario?.id ? (
                <div>
                  <button
                    type="button"
                    onClick={onSelectRecommendedScenario}
                    style={{
                      border: '1px solid var(--border)',
                      borderRadius: 999,
                      background: 'white',
                      color: 'var(--text)',
                      padding: '8px 12px',
                      fontWeight: 800,
                    }}
                  >
                    Open next uncovered scenario
                  </button>
                </div>
              ) : null}
              {suiteScenarioCoverage.missing_scenarios?.length ? (
                <div style={{ display: 'grid', gap: 8 }}>
                  <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13, fontWeight: 800 }}>
                    Focus an uncovered scenario
                  </p>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {suiteScenarioCoverage.missing_scenarios.slice(0, 4).map((scenario) => (
                      <button
                        key={scenario.id ?? scenario.title}
                        type="button"
                        onClick={() => onSelectCoverageScenario(scenario.id ?? '', scenario.title ?? scenario.id ?? undefined)}
                        style={{
                          border: '1px solid var(--border)',
                          borderRadius: 999,
                          background: 'var(--surface)',
                          color: 'var(--text)',
                          padding: '8px 12px',
                          fontWeight: 700,
                        }}
                      >
                        {scenario.title ?? scenario.id}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
              {suiteScenarioCoverage.out_of_suite_scenarios?.length ? (
                <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>
                  Outside suite history: {suiteScenarioCoverage.out_of_suite_scenarios.map((scenario) => scenario.title ?? scenario.id).join(', ')}.
                </p>
              ) : null}
            </div>
          ) : null}
          {savedRuns.length ? (
            <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--muted)', display: 'grid', gap: 8 }}>
              {savedRuns.slice(0, 4).map((run, index) => (
                <li key={run.id}>
                  <span>
                    {run.id}: {run.report.scenario_title ?? run.report.run_id ?? 'benchmark run'} ({run.report.overall_score ?? run.report.score ?? 'n/a'})
                  </span>
                  <div style={{ marginTop: 4, fontSize: 13 }}>
                    {metadataChangeSummary(run.report.run_metadata, savedRuns[index + 1]?.report.run_metadata)}
                  </div>
                  <div style={{ marginTop: 4, fontSize: 13, color: regressionDeltaColor(run.artifacts?.regression_delta?.status) }}>
                    {regressionDeltaSummary(run.artifacts?.regression_delta)}
                  </div>
                  <div style={{ marginTop: 4, fontSize: 13, color: run.artifacts?.vcon_export?.available ? 'var(--success-text)' : 'var(--muted)' }}>
                    {savedRunVconSummary(run.artifacts?.vcon_export)}
                  </div>
                  <div style={{ marginTop: 4, fontSize: 13, color: run.artifacts?.audit_artifacts?.ready_for_export ? 'var(--success-text)' : 'var(--muted)' }}>
                    {savedRunAuditArtifactSummary(run.artifacts?.audit_artifacts)}
                  </div>
                  <div style={{ marginTop: 4, fontSize: 13, color: run.artifacts?.contract_artifacts?.available ? 'var(--success-text)' : 'var(--muted)' }}>
                    {savedRunContractArtifactSummary(run.artifacts?.contract_artifacts)}
                  </div>
                  <div style={{ marginTop: 4, fontSize: 12, color: 'var(--muted)' }}>
                    {run.firestore_path}
                  </div>
                  <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      onClick={() => onLoadSavedRun(run)}
                      style={{
                        border: '1px solid var(--border)',
                        borderRadius: 8,
                        background: 'white',
                        color: 'var(--text)',
                        padding: '6px 10px',
                        fontWeight: 800,
                      }}
                    >
                      Load run
                    </button>
                    <button
                      type="button"
                      onClick={() => void onRetrySavedRun(run)}
                      disabled={isRunning}
                      style={{
                        border: '1px solid var(--border)',
                        borderRadius: 8,
                        background: 'white',
                        color: 'var(--text)',
                        padding: '6px 10px',
                        fontWeight: 800,
                      }}
                    >
                      Retry run
                    </button>
                    <button
                      type="button"
                      onClick={() => void onExportRun(run.id)}
                      style={{
                        border: '1px solid var(--border)',
                        borderRadius: 8,
                        background: 'white',
                        color: 'var(--text)',
                        padding: '6px 10px',
                        fontWeight: 800,
                      }}
                    >
                      Export JSON
                    </button>
                    <button
                      type="button"
                      onClick={() => void onExportRunAuditArtifacts(run)}
                      style={{
                        border: '1px solid var(--border)',
                        borderRadius: 8,
                        background: 'white',
                        color: 'var(--text)',
                        padding: '6px 10px',
                        fontWeight: 800,
                      }}
                    >
                      Export audit artifacts
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p style={{ margin: 0, color: 'var(--muted)' }}>Run this scenario, sign up, then save it to build focused history.</p>
          )}
          {auditEvents.length ? (
            <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'var(--panel-alt)', display: 'grid', gap: 8 }} aria-label="Project audit trail">
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <strong>Project audit trail</strong>
                <span style={{ color: 'var(--muted)', fontWeight: 800 }}>{auditEvents.length} recent</span>
              </div>
              <ol style={{ margin: 0, paddingLeft: 18, color: 'var(--muted)', display: 'grid', gap: 4 }}>
                {auditEvents.slice(0, 5).map((event) => (
                  <li key={event.id}>
                    <strong style={{ color: 'var(--ink)' }}>{event.event_type}</strong>
                    {': '}
                    {auditEventSummary(event)}
                    {' '}
                    <span>{formatHistoryDate(event.created_at)}</span>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          {exportMessage ? <p style={{ margin: 0, color: 'var(--muted)' }}>{exportMessage}</p> : null}
        </div>

        <div className="card" style={{ padding: 20, display: 'grid', gap: 12 }} aria-label="Suite run history">
          <p className="eyebrow">Suite runs</p>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0 }}>
              {userId ? `${suiteRuns.length} suite runs for ${selectedSuite?.title ?? projectId}` : 'Signup required'}
            </h3>
            {userId ? (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--muted)', fontWeight: 800 }}>
                  {suiteRuns.filter((run) => run.status === 'running' || run.status === 'queued').length} active
                </span>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--muted)', fontWeight: 800 }}>
                  Status
                  <select
                    value={suiteRunStatusFilter}
                    onChange={(event) => setSuiteRunStatusFilter(event.target.value)}
                    aria-label="Filter suite runs by status"
                    style={{ border: '1px solid var(--border)', borderRadius: 8, background: 'white', color: 'var(--text)', padding: '8px 10px', fontWeight: 800 }}
                  >
                    <option value="">All</option>
                    <option value="queued">Queued</option>
                    <option value="running">Running</option>
                    <option value="completed">Completed</option>
                    <option value="needs_review">Needs review</option>
                    <option value="failed">Failed</option>
                  </select>
                </label>
                <button
                  type="button"
                  onClick={() => void onRefreshSuiteRuns()}
                  disabled={isRefreshingSuiteRuns}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    background: isRefreshingSuiteRuns ? 'var(--panel)' : 'white',
                    color: isRefreshingSuiteRuns ? 'var(--muted)' : 'var(--text)',
                    padding: '8px 12px',
                    fontWeight: 800,
                    cursor: isRefreshingSuiteRuns ? 'wait' : 'pointer',
                  }}
                >
                  {isRefreshingSuiteRuns ? 'Refreshing...' : 'Refresh suite runs'}
                </button>
                <button
                  type="button"
                  onClick={() => void onExportBenchmarkSuiteRunHistory()}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    background: 'white',
                    color: 'var(--text)',
                    padding: '8px 12px',
                    fontWeight: 800,
                  }}
                >
                  Export suite history
                </button>
              </div>
            ) : null}
          </div>
          {visibleSuiteHistorySummary ? (
            <p style={{ margin: 0, color: regressionDeltaColor(visibleSuiteHistorySummary.latest_trend ?? undefined), fontWeight: 800 }}>
              Visible {suiteHistoryExportSummary(visibleSuiteHistorySummary)}
            </p>
          ) : null}
          {latestSuiteRunUpdate ? (
            <p style={{ margin: 0, color: 'var(--muted)' }} aria-label="Latest suite run update">
              Latest suite run update {formatHistoryDate(latestSuiteRunUpdate)}
            </p>
          ) : null}
          {suiteRuns.length ? (
            <div style={{ display: 'grid', gap: 10 }}>
              {suiteRuns.slice(0, 4).map((run) => {
                const scenarioSummaries = run.artifacts?.scenario_summaries ?? [];
                const reliability = suiteReliabilityMetrics(run);
                const lifecycleSummary = suiteRunLifecycleSummary(run.run_lifecycle);
                const lifecycleTimeline = suiteRunLifecycleTimeline(run.run_lifecycle);
                const failureCategorySummary = suiteRunFailureCategorySummary(scenarioSummaries);
                return (
                  <article
                    key={run.suite_run_id}
                    style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'white', display: 'grid', gap: 8 }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                      <strong>{suiteRunTitle(run)}</strong>
                      <span style={{ color: suiteRunStatusColor(run.status), fontWeight: 900, textTransform: 'capitalize' }}>
                        {run.status.replace(/_/g, ' ')}
                      </span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 8 }}>
                      <AuditFact label="Scenarios" value={String(run.scenario_count)} />
                      <AuditFact label="Passing" value={String(run.pass_count)} />
                      <AuditFact label="Review" value={String(run.needs_review_count)} />
                      <AuditFact label="Average" value={String(run.average_score ?? 'n/a')} />
                      <AuditFact label="Progress" value={formatSuiteRunProgress(run.progress)} />
                      <AuditFact label="Pass@1" value={formatMetricPercent(reliability.pass_at_1)} />
                      <AuditFact label="Pass@k" value={formatMetricPercent(reliability.pass_at_k)} />
                      <AuditFact label="Pass^k" value={formatMetricPercent(reliability.pass_all_k)} />
                    </div>
                    <p style={{ margin: 0, color: 'var(--muted)' }}>
                      Retained until {formatHistoryDate(run.retention?.retained_until)}. Updated {formatHistoryDate(run.updated_at)}.
                    </p>
                    {lifecycleSummary ? (
                      <div style={{ display: 'grid', gap: 6 }}>
                        <p style={{ margin: 0, color: 'var(--muted)' }}>
                          {lifecycleSummary}
                        </p>
                        {lifecycleTimeline.length ? (
                          <ol aria-label={`Audit timeline for ${suiteRunTitle(run)}`} style={{ margin: 0, paddingLeft: 18, color: 'var(--muted)', display: 'grid', gap: 4 }}>
                            {lifecycleTimeline.map((transition, index) => (
                              <li key={`${transition.to ?? 'status'}-${transition.at ?? index}`}>
                                <strong style={{ color: 'var(--ink)', textTransform: 'capitalize' }}>{(transition.to ?? 'unknown').replace(/_/g, ' ')}</strong>
                                {transition.at ? ` at ${formatHistoryDate(transition.at)}` : ''}
                                {transition.reason ? ` - ${transition.reason}` : ''}
                              </li>
                            ))}
                          </ol>
                        ) : null}
                      </div>
                    ) : null}
                    <p style={{ margin: 0, color: run.artifacts?.vcon_export?.available ? 'var(--success-text)' : 'var(--muted)' }}>
                      {suiteRunVconSummary(run)}
                    </p>
                    {reliability.framework ? (
                      <p style={{ margin: 0, color: 'var(--muted)' }}>
                        EVA-style reliability: {formatMetricPercent(reliability.accuracy_score)} accuracy, {formatMetricPercent(reliability.experience_signal_coverage)} experience coverage, {reliability.average_turn_count ?? 0} avg turns.
                      </p>
                    ) : null}
                    {reliability.perturbation_tags?.length ? (
                      <p style={{ margin: 0, color: 'var(--muted)' }}>
                        Robustness tags: {reliability.perturbation_tags.join(', ')}.
                      </p>
                    ) : null}
                    {failureCategorySummary ? (
                      <p style={{ margin: 0, color: 'var(--muted)' }}>
                        {failureCategorySummary}
                      </p>
                    ) : null}
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <button
                        type="button"
                        onClick={() => onLoadSuiteRun(run)}
                        disabled={!run.suite_report?.scenario_runs?.length}
                        style={{
                          border: '1px solid var(--border)',
                          borderRadius: 8,
                          background: run.suite_report?.scenario_runs?.length ? 'white' : 'var(--panel)',
                          color: run.suite_report?.scenario_runs?.length ? 'var(--text)' : 'var(--muted)',
                          padding: '6px 10px',
                          fontWeight: 800,
                          cursor: run.suite_report?.scenario_runs?.length ? 'pointer' : 'not-allowed',
                        }}
                      >
                        Load suite run
                      </button>
                      <button
                        type="button"
                        onClick={() => void onExportRetainedSuiteAuditArtifacts(run.suite_run_id)}
                        disabled={!run.suite_report?.scenario_runs?.length}
                        style={{
                          border: '1px solid var(--border)',
                          borderRadius: 8,
                          background: run.suite_report?.scenario_runs?.length ? 'white' : 'var(--panel)',
                          color: run.suite_report?.scenario_runs?.length ? 'var(--text)' : 'var(--muted)',
                          padding: '6px 10px',
                          fontWeight: 800,
                          cursor: run.suite_report?.scenario_runs?.length ? 'pointer' : 'not-allowed',
                        }}
                      >
                        Export suite audit artifacts
                      </button>
                      <button
                        type="button"
                        onClick={() => void onExportRetainedSuiteVconBundle(run.suite_run_id)}
                        disabled={!run.artifacts?.vcon_export?.available}
                        style={{
                          border: '1px solid var(--border)',
                          borderRadius: 8,
                          background: run.artifacts?.vcon_export?.available ? 'white' : 'var(--panel)',
                          color: run.artifacts?.vcon_export?.available ? 'var(--text)' : 'var(--muted)',
                          padding: '6px 10px',
                          fontWeight: 800,
                          cursor: run.artifacts?.vcon_export?.available ? 'pointer' : 'not-allowed',
                        }}
                      >
                        Export retained vCon bundle
                      </button>
                    </div>
                    {scenarioSummaries.length ? (
                      <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--muted)', display: 'grid', gap: 4 }}>
                        {scenarioSummaries.slice(0, 3).map((scenario) => (
                          <li key={`${run.suite_run_id}:${scenario.run_id ?? scenario.scenario_id}`}>
                            {scenario.scenario_id ?? 'scenario'}: {scenario.status ?? 'unknown'} / {scenario.overall_score ?? 'n/a'}
                            {scenario.run_id ? ` (${scenario.run_id})` : ''}
                            {scenarioFailureCategorySummary(scenario.failure_categories)}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p style={{ margin: 0, color: 'var(--muted)' }}>Scenario artifacts appear when the suite run completes.</p>
                    )}
                  </article>
                );
              })}
            </div>
          ) : (
            <p style={{ margin: 0, color: 'var(--muted)' }}>Run a suite while signed in to retain suite-level history and child report links.</p>
          )}
        </div>
      </section>
      ) : null}
    </section>
  );
}

function ScenarioList({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <p style={{ margin: '0 0 6px', fontWeight: 800 }}>{title}</p>
      {items.length ? (
        <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--muted)', lineHeight: 1.5 }}>
          {items.map((item) => <li key={item}>{item}</li>)}
        </ul>
      ) : (
        <p style={{ margin: 0, color: 'var(--muted)' }}>Not specified.</p>
      )}
    </div>
  );
}

function ScoreTile({ label, score }: { label: string; score?: number | null }) {
  const hasScore = typeof score === 'number' && Number.isFinite(score);
  return (
    <div aria-label={`${label} score`} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14 }}>
      <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 13 }}>{label}</p>
      <p style={{ margin: 0, fontSize: 24, fontWeight: 900, color: hasScore ? scoreColor(score) : 'var(--muted)' }}>
        {hasScore ? score : 'n/a'}
      </p>
      {!hasScore ? (
        <p style={{ margin: '6px 0 0', color: 'var(--muted)', fontSize: 12, lineHeight: 1.35 }}>
          Not measured from this evidence
        </p>
      ) : null}
    </div>
  );
}

function RunMetadataPanel({ metadata }: { metadata?: RunMetadata }) {
  const entries = metadataEntries(metadata);

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, display: 'grid', gap: 8 }}>
      <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13, fontWeight: 800 }}>Run labels</p>
      {entries.length ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {entries.map((item) => (
            <span
              key={item.key}
              style={{
                border: '1px solid var(--border)',
                borderRadius: 8,
                background: 'var(--panel-alt)',
                color: 'var(--text)',
                padding: '6px 8px',
                fontSize: 13,
                fontWeight: 760,
              }}
            >
              {item.label}: {item.value}
            </span>
          ))}
        </div>
      ) : (
        <p style={{ margin: 0, color: 'var(--muted)' }}>No prompt, model, or version labels captured.</p>
      )}
    </div>
  );
}


function SimulationValidationPanel({ validation, onRegenerate, isRegenerating }: { validation?: SimulationValidation; onRegenerate: () => void; isRegenerating: boolean }) {
  if (!validation) return null;

  const ready = validation.ready_for_scoring ?? validation.status === 'ready_for_scoring';
  const missing = validation.missing_required_actions ?? [];
  const artifacts = validation.artifact_presence ?? {};

  return (
    <div style={{ border: `1px solid ${ready ? 'var(--success-border)' : 'var(--error-border)'}`, borderRadius: 8, padding: 14, display: 'grid', gap: 12, background: ready ? 'var(--success-bg)' : 'var(--error-bg)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <p style={{ margin: '0 0 4px', color: ready ? 'var(--success-text)' : 'var(--error-text)', fontSize: 13, fontWeight: 800 }}>Simulation validation</p>
          <p style={{ margin: 0, fontWeight: 850 }}>{ready ? 'Ready for scoring' : 'Needs regeneration before scoring'}</p>
        </div>
        {!ready ? (
          <button type="button" onClick={onRegenerate} disabled={isRegenerating} style={{ border: '1px solid var(--border)', borderRadius: 8, background: 'white', color: 'var(--text)', padding: '8px 12px', fontWeight: 800 }}>
            {isRegenerating ? 'Regenerating...' : 'Regenerate scenario'}
          </button>
        ) : null}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        <AuditFact label="Transcript" value={formatBoolean(artifacts.transcript)} />
        <AuditFact label="Action trace" value={formatBoolean(artifacts.action_trace)} />
        <AuditFact label="Final state" value={formatBoolean(artifacts.final_state)} />
        <AuditFact label="Final complete" value={formatBoolean(validation.final_state_complete)} />
        <AuditFact label="Completed actions" value={String(validation.completed_required_action_count ?? 'n/a')} />
      </div>
      {missing.length ? <p style={{ margin: 0, color: 'var(--error-text)' }}>Missing required actions: {missing.join('; ')}</p> : null}
    </div>
  );
}

function GroupCallPanel({ summary }: { summary?: GroupCallSummary | null }) {
  if (!summary) return null;

  const speakers = summary.speakers?.length ? summary.speakers.join(', ') : 'None captured';

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, display: 'grid', gap: 12 }}>
      <div>
        <p style={{ margin: '0 0 4px', color: 'var(--muted)', fontSize: 13, fontWeight: 800 }}>Group call evidence</p>
        <p style={{ margin: 0, fontWeight: 850 }}>{speakers}</p>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        <AuditFact label="Speakers" value={String(summary.speaker_count ?? 0)} />
        <AuditFact label="Messages" value={String(summary.message_count ?? 0)} />
        <AuditFact label="Decisions" value={String(summary.decision_count ?? 0)} />
        <AuditFact label="Commitments" value={String(summary.commitment_count ?? 0)} />
        <AuditFact label="Follow-ups" value={String(summary.follow_up_count ?? 0)} />
        <AuditFact label="Action items" value={String(summary.action_item_count ?? 0)} />
      </div>
    </div>
  );
}

function VoiceInteractionPanel({ summary }: { summary?: VoiceInteractionSummary | null }) {
  if (!summary) return null;

  const signalCount = (summary.interruption_signal_count ?? 0)
    + (summary.correction_signal_count ?? 0)
    + (summary.handoff_signal_count ?? 0)
    + (summary.action_trace_event_count ?? 0);
  const hasMedia = Boolean(summary.media?.recording_url || summary.media?.mime_type);
  const hasTiming = typeof summary.duration_ms === 'number'
    || typeof summary.average_latency_ms === 'number'
    || typeof summary.max_latency_ms === 'number'
    || typeof summary.packet_loss_percent === 'number'
    || typeof summary.jitter_ms === 'number';
  // Don't show an empty voice card for plain text transcript evals.
  if (signalCount === 0 && !hasMedia && !hasTiming) return null;

  const status = signalCount > 0 ? 'Voice turn signals captured' : 'No interruption or correction signals captured';

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, display: 'grid', gap: 12 }}>
      <div>
        <p style={{ margin: '0 0 4px', color: 'var(--muted)', fontSize: 13, fontWeight: 800 }}>Voice interaction evidence</p>
        <p style={{ margin: 0, fontWeight: 850 }}>{status}</p>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        <AuditFact label="Turns" value={String(summary.turn_count ?? 0)} />
        <AuditFact label="Interruptions" value={String(summary.interruption_signal_count ?? 0)} />
        <AuditFact label="Corrections" value={String(summary.correction_signal_count ?? 0)} />
        <AuditFact label="Handoffs" value={String(summary.handoff_signal_count ?? 0)} />
        <AuditFact label="Action events" value={String(summary.action_trace_event_count ?? 0)} />
        {typeof summary.duration_ms === 'number' ? <AuditFact label="Duration" value={formatMilliseconds(summary.duration_ms)} /> : null}
        {typeof summary.average_latency_ms === 'number' ? <AuditFact label="Avg latency" value={formatMilliseconds(summary.average_latency_ms)} /> : null}
        {typeof summary.max_latency_ms === 'number' ? <AuditFact label="Max latency" value={formatMilliseconds(summary.max_latency_ms)} /> : null}
        {typeof summary.packet_loss_percent === 'number' ? <AuditFact label="Packet loss" value={`${summary.packet_loss_percent}%`} /> : null}
        {typeof summary.jitter_ms === 'number' ? <AuditFact label="Jitter" value={formatMilliseconds(summary.jitter_ms)} /> : null}
        {summary.media?.recording_url ? <AuditFact label="Recording" value={summary.media.recording_url} /> : null}
        {summary.media?.mime_type ? <AuditFact label="Media type" value={summary.media.mime_type} /> : null}
      </div>
    </div>
  );
}

function formatMilliseconds(value: number) {
  return `${Number.isInteger(value) ? value : value.toFixed(1)} ms`;
}

function EvidenceAuditPanel({ summary }: { summary?: EvidenceAuditSummary }) {
  const artifactTypes = summary?.input_artifact_types ?? [];
  const metadataLabels = summary?.metadata_labels ?? [];
  const exportReady = summary?.export_readiness?.ready;

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14, display: 'grid', gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <p style={{ margin: '0 0 4px', color: 'var(--muted)', fontSize: 13, fontWeight: 800 }}>Evidence audit</p>
          <p style={{ margin: 0, fontWeight: 850 }}>
            {exportReady ? 'Export ready' : summary ? 'Needs evidence before export' : 'Not captured'}
          </p>
        </div>
        <span
          style={{
            border: `1px solid ${exportReady ? 'var(--success-border)' : 'var(--border)'}`,
            borderRadius: 8,
            background: exportReady ? 'var(--success-bg)' : 'var(--panel-alt)',
            color: exportReady ? 'var(--success-text)' : 'var(--muted)',
            padding: '6px 8px',
            fontSize: 13,
            fontWeight: 800,
            alignSelf: 'start',
          }}
        >
          {summary?.evaluator_version ?? 'no evaluator version'}
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        <AuditFact label="Transcript" value={formatBoolean(summary?.transcript_present)} />
        <AuditFact label="Action trace" value={formatBoolean(summary?.action_trace_present)} />
        <AuditFact label="Final state" value={formatBoolean(summary?.final_state_present)} />
        <AuditFact label="Evaluated" value={formatAuditTimestamp(summary?.evaluated_at)} />
      </div>

      <div style={{ display: 'grid', gap: 6 }}>
        <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>
          Artifacts: {artifactTypes.length ? artifactTypes.map(artifactLabel).join(', ') : 'none'}
        </p>
        <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>
          Labels: {metadataLabels.length ? metadataLabels.map(artifactLabel).join(', ') : 'none'}
        </p>
      </div>
    </div>
  );
}

function AuditFact({ label, value }: { label: string; value: string }) {
  return (
    <div
      aria-label={`${label}: ${value}`}
      style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10, background: 'var(--panel-alt)' }}
    >
      <p style={{ margin: '0 0 4px', color: 'var(--muted)', fontSize: 12, fontWeight: 800 }}>{label}</p>
      <p style={{ margin: 0, fontWeight: 800 }}>{value}</p>
    </div>
  );
}

function ReportList({ title, items, empty }: { title: string; items?: string[]; empty: string }) {
  return (
    <div>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      {items?.length ? (
        <ul style={{ marginBottom: 0 }}>
          {items.map((item) => <li key={item}>{item}</li>)}
        </ul>
      ) : (
        <p style={{ margin: 0, color: 'var(--muted)' }}>{empty}</p>
      )}
    </div>
  );
}
