'use client';

import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

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
  task_completion_score?: number;
  required_action_score?: number;
  forbidden_action_score?: number;
  final_state_score?: number;
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
  covered_scenarios?: Array<{ id?: string; title?: string }>;
  missing_scenarios?: Array<{ id?: string; title?: string }>;
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

interface CheckoutGate {
  status: 'ready' | 'blocked';
  plan: 'starter' | 'team';
  stripe_price_id?: string | null;
  checkout_url?: string | null;
  message: string;
  metadata?: Record<string, string>;
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

interface JudgeGate {
  status: 'blocked' | 'ready';
  required_plan: PricingPlan['id'];
  credits: number;
  message: string;
  evidence_citations: string[];
  spend_control?: {
    estimated_credits?: number;
    daily_credit_limit?: number;
    reserved_daily_credits?: number;
    remaining_daily_credits?: number;
    provider?: string;
    provider_configured?: boolean;
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

async function handleJson<T>(response: Response): Promise<T> {
  const text = await response.text();

  if (!response.ok) {
    let message = text || `Request failed with ${response.status}`;
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      message = parsed.detail || message;
    } catch {
      // Keep plain-text fallback.
    }
    throw new Error(message);
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
    const scenarios = Array.isArray(suite.scenarios) ? suite.scenarios.map((scenario) => normalizeScenario(scenario, id)) : [];

    return {
      id,
      title: String(suite.title ?? suite.name ?? 'Untitled suite'),
      description: typeof suite.description === 'string' ? suite.description : null,
      scenarios,
    };
  });
}

async function fetchBenchmarkSuites(): Promise<BenchmarkSuite[]> {
  const suites = await handleJson<unknown>(await fetch(`${getApiBase()}/api/benchmarks/suites`, { cache: 'no-store' }));
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
  action_trace: unknown;
  final_state: unknown;
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

async function fetchProductConfig(): Promise<ProductConfig> {
  return handleJson<ProductConfig>(await fetch(`${getApiBase()}/api/product/config`, { cache: 'no-store' }));
}

async function requestCheckoutGate(payload: {
  plan: 'starter' | 'team';
  user_id: string;
  project_id: string;
  success_url?: string;
  cancel_url?: string;
}) {
  return handleJson<CheckoutGate>(
    await fetch(`${getApiBase()}/api/product/checkout`, {
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
  const limit = spendControl.daily_credit_limit;
  const provider = spendControl.provider ?? 'judge provider';
  const providerStatus = spendControl.provider_configured ? 'configured' : 'not configured';

  return `${estimated} credits estimated; ${remaining ?? 'unknown'} of ${limit ?? 'unknown'} daily credits available; ${provider} ${providerStatus}.`;
}

function EvidenceItem({ item }: { item: string | JsonRecord }) {
  if (typeof item === 'string') {
    return <li>{item}</li>;
  }

  return <li><code>{JSON.stringify(item)}</code></li>;
}

function cleanRunMetadata(metadata: RunMetadata): RunMetadata {
  return Object.fromEntries(
    Object.entries(metadata).map(([key, value]) => [key, value?.trim()]).filter(([, value]) => Boolean(value)),
  ) as RunMetadata;
}

function metadataEntries(metadata?: RunMetadata) {
  const labels: Record<keyof RunMetadata, string> = {
    agent_version: 'Agent',
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
    return `Audit export ready: ${artifactTypes} (${summary.evaluator_version ?? 'unknown evaluator'}).`;
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
  if (typeof summary.scenario_count !== 'number') {
    const coveredPreview = coveredScenarios.slice(0, 2).join(', ');
    return `${summary.covered_scenario_count ?? 0} distinct scenarios covered${coveredPreview ? `: ${coveredPreview}` : ''}.`;
  }

  const coverage = typeof summary.coverage_percent === 'number' ? `${summary.coverage_percent}%` : 'n/a';
  const missingScenarios = summary.missing_scenarios?.length
    ? summary.missing_scenarios.map((scenario) => scenario.title ?? scenario.id).filter(Boolean)
    : summary.missing_scenario_ids ?? [];
  const missingCount = missingScenarios.length;
  const missingPreview = missingScenarios.slice(0, 2).join(', ');
  const nextScenario = summary.recommended_next_scenario?.title ?? summary.recommended_next_scenario?.id;
  const nextStep = nextScenario
    ? summary.coverage_status === 'empty'
      ? ` Start with ${nextScenario}.`
      : ` Next: ${nextScenario}.`
    : '';
  const coveredPreview = !missingCount && coveredScenarios.length ? ` Covered: ${coveredScenarios.slice(0, 2).join(', ')}.` : '';
  if (summary.coverage_status === 'complete' || (!missingCount && summary.covered_scenario_count === summary.scenario_count)) {
    return `${summary.covered_scenario_count ?? 0}/${summary.scenario_count} suite scenarios covered (${coverage}); all scenarios covered.${coveredPreview}`;
  }
  return `${summary.covered_scenario_count ?? 0}/${summary.scenario_count} suite scenarios covered (${coverage}); ${missingCount} missing${missingPreview ? `: ${missingPreview}` : ''}.${nextStep}${coveredPreview}`;
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

function formatReportBrief(report: BenchmarkReport, fallbackScenarioTitle?: string, regressionDelta?: RegressionDelta | null) {
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
    `Missing actions: ${missingActions}`,
    `Forbidden actions observed: ${forbiddenActions}`,
    `Suggested fixes: ${suggestedFixes}`,
  ].join('\n');
}

function reportActionPlan(report: BenchmarkReport, regressionDelta?: RegressionDelta | null) {
  const verdict = (report.verdict ?? report.overall ?? '').toLowerCase();
  const score = report.score ?? report.overall_score;
  const missingCount = report.missing_actions?.length ?? 0;
  const forbiddenCount = (report.forbidden_actions_observed?.length ?? report.forbidden_action_hits?.length) ?? 0;
  const failureCategory = report.failure_categories?.[0]?.replace(/_/g, ' ') ?? null;
  const suggestedFix = report.suggested_fixes?.[0] ?? report.recommendations?.[0] ?? null;
  const isPass = verdict === 'pass' || (typeof score === 'number' && score >= 80 && missingCount === 0 && forbiddenCount === 0);

  const headline = isPass ? 'Ready for release review' : 'Needs operator review';
  const primaryRisk = isPass
    ? 'No blocking failure category was reported for this scenario.'
    : failureCategory ?? (missingCount ? `${missingCount} required action${missingCount === 1 ? '' : 's'} missing` : 'Benchmark evidence needs review');
  const nextStep = isPass
    ? 'Save this run as the baseline, then compare the next prompt or model change against it.'
    : suggestedFix ?? 'Fix the highest-risk failure, regenerate evidence, and rerun this scenario before release.';
  const regression = regressionDelta ? regressionDeltaSummary(regressionDelta) : 'Save the run to establish regression tracking.';

  return { headline, primaryRisk, nextStep, regression };
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

export function BenchmarkRunner() {
  const loadingSavedRunRef = useRef(false);
  const [suites, setSuites] = useState<BenchmarkSuite[]>([]);
  const [productConfig, setProductConfig] = useState<ProductConfig | null>(null);
  const [selectedSuiteId, setSelectedSuiteId] = useState('');
  const [selectedScenarioId, setSelectedScenarioId] = useState('');
  const [transcript, setTranscript] = useState('');
  const [actionTrace, setActionTrace] = useState('');
  const [finalState, setFinalState] = useState('');
  const [callEvidence, setCallEvidence] = useState('');
  const [groupCall, setGroupCall] = useState('');
  const [vconEvidence, setVconEvidence] = useState('');
  const [agentProfile, setAgentProfile] = useState('mock text agent');
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
  const [plan, setPlan] = useState<PricingPlan['id']>('free');
  const [savedRuns, setSavedRuns] = useState<SavedRun[]>([]);
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
  const [checkoutMessage, setCheckoutMessage] = useState<string | null>(null);
  const [judgeGate, setJudgeGate] = useState<JudgeGate | null>(null);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [isSimulating, setIsSimulating] = useState(false);
  const [isEnqueueingSuite, setIsEnqueueingSuite] = useState(false);

  const selectedSuite = useMemo(
    () => suites.find((suite) => suite.id === selectedSuiteId) ?? suites[0] ?? null,
    [selectedSuiteId, suites],
  );
  const selectedScenario = useMemo(
    () => selectedSuite?.scenarios.find((scenario) => scenario.id === selectedScenarioId) ?? selectedSuite?.scenarios[0] ?? null,
    [selectedScenarioId, selectedSuite],
  );

  useEffect(() => {
    let isMounted = true;

    async function loadSuites() {
      setIsLoading(true);
      setLoadError(null);

      try {
        const nextSuites = await fetchBenchmarkSuites();
        const nextConfig = await fetchProductConfig();
        if (!isMounted) return;
        setSuites(nextSuites);
        setProductConfig(nextConfig);
        setSelectedSuiteId(nextSuites[0]?.id ?? '');
        setSelectedScenarioId(nextSuites[0]?.scenarios[0]?.id ?? '');
      } catch (err) {
        if (!isMounted) return;
        setLoadError(err instanceof Error ? err.message : 'Could not load benchmark suites');
      } finally {
        if (isMounted) setIsLoading(false);
      }
    }

    void loadSuites();

    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const storedUser = window.localStorage.getItem('conversation-evals-demo-user');
    const storedProject = window.localStorage.getItem('conversation-evals-demo-project');
    const storedPlan = window.localStorage.getItem('conversation-evals-demo-plan') as PricingPlan['id'] | null;
    if (storedUser) setUserId(storedUser);
    if (storedProject) setProjectId(storedProject);
    if (storedPlan && ['free', 'starter', 'team', 'business'].includes(storedPlan)) setPlan(storedPlan);
  }, []);

  useEffect(() => {
    if (!userId) {
      setSavedRuns([]);
      setAuditEvents([]);
      setSuiteRuns([]);
      setProjectRegressionSummary(null);
      setScenarioRegressionSummary(null);
      return;
    }

    let isMounted = true;
    Promise.all([
      listSavedRuns(userId, projectId, selectedSuite?.id, selectedScenario?.id),
      listAuditEvents(userId, projectId).catch(() => []),
      listBenchmarkSuiteRuns(userId, projectId, selectedSuite?.id, suiteRunStatusFilter).catch(() => []),
      fetchProjectRegressionSummary(userId, projectId).catch(() => null),
      selectedSuite?.id && selectedScenario?.id
        ? fetchProjectRegressionSummary(userId, projectId, selectedSuite.id, selectedScenario.id).catch(() => null)
        : Promise.resolve(null),
    ])
      .then(([runs, events, nextSuiteRuns, summary, scenarioSummary]) => {
        if (!isMounted) return;
        setSavedRuns(runs);
        setAuditEvents(events);
        setSuiteRuns(nextSuiteRuns);
        setProjectRegressionSummary(summary);
        setScenarioRegressionSummary(scenarioSummary);
      })
      .catch(() => {
        if (!isMounted) return;
        setSavedRuns([]);
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

    setTranscript(selectedScenario.sample_transcript ?? '');
    setActionTrace(stringifyEditable(selectedScenario.sample_action_trace, '[]'));
    setFinalState(stringifyEditable(selectedScenario.sample_final_state ?? selectedScenario.expected_final_state, '{}'));
    setReport(null);
    setSuiteSimulation(null);
    setSaveMessage(null);
    setJudgeGate(null);
    setCopyMessage(null);
    setRunError(null);
  }, [selectedScenario]);

  useEffect(() => {
    if (!userId || !projectId || !selectedSuite?.id) return;
    const hasActiveSuiteRun = suiteRuns.some((run) => isActiveSuiteRunStatus(run.status));
    if (!hasActiveSuiteRun) return;

    const interval = window.setInterval(() => {
      listBenchmarkSuiteRuns(userId, projectId, selectedSuite.id, suiteRunStatusFilter)
        .then(setSuiteRuns)
        .catch(() => undefined);
    }, 4000);

    return () => window.clearInterval(interval);
  }, [projectId, selectedSuite?.id, suiteRunStatusFilter, suiteRuns, userId]);

  function signInDemo() {
    const nextUser = `demo-user-${Math.random().toString(36).slice(2, 8)}`;
    setUserId(nextUser);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem('conversation-evals-demo-user', nextUser);
      window.localStorage.setItem('conversation-evals-demo-project', projectId);
      window.localStorage.setItem('conversation-evals-demo-plan', plan);
    }
    setSaveMessage('Signed in with local Firebase-ready demo identity. Real Firebase credentials can replace this without changing the product flow.');
  }

  function updatePlan(nextPlan: PricingPlan['id']) {
    setPlan(nextPlan);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem('conversation-evals-demo-plan', nextPlan);
    }
  }

  async function onSelectPlan(nextPlan: PricingPlan['id']) {
    updatePlan(nextPlan);
    setCheckoutMessage(null);

    if (nextPlan !== 'starter' && nextPlan !== 'team') return;

    const checkoutUserId = userId || 'anonymous-upgrade-preview';
    try {
      const origin = typeof window !== 'undefined' ? window.location.origin : undefined;
      const gate = await requestCheckoutGate({
        plan: nextPlan,
        user_id: checkoutUserId,
        project_id: projectId,
        success_url: origin ? `${origin}/benchmarks?checkout=success` : undefined,
        cancel_url: origin ? `${origin}/benchmarks?checkout=cancelled` : undefined,
      });
      const planName = nextPlan === 'starter' ? 'Starter' : 'Team';
      if (gate.status === 'ready') {
        setCheckoutMessage(`${planName} checkout ready${gate.checkout_url ? `: ${gate.checkout_url}` : '.'}`);
      } else {
        setCheckoutMessage(gate.message);
      }
    } catch (err) {
      setCheckoutMessage(err instanceof Error ? err.message : 'Could not check billing readiness.');
    }
  }

  async function refreshAuditTrail() {
    if (!userId) return;
    try {
      setAuditEvents(await listAuditEvents(userId, projectId));
    } catch {
      setAuditEvents([]);
    }
  }

  async function onSaveRun() {
    if (!report) return;
    if (!userId) {
      setSaveMessage('Sign up first to save projects and run history.');
      return;
    }

    try {
      const saved = await saveBenchmarkRun({ user_id: userId, project_id: projectId, plan, report, transcript });
      setSavedRuns((current) => [saved, ...current.filter((run) => run.id !== saved.id)]);
      fetchProjectRegressionSummary(userId, projectId)
        .then(setProjectRegressionSummary)
        .catch(() => setProjectRegressionSummary(null));
      fetchProjectRegressionSummary(userId, projectId, saved.report.suite_id, saved.report.scenario_id)
        .then(setScenarioRegressionSummary)
        .catch(() => setScenarioRegressionSummary(null));
      await refreshAuditTrail();
      setSaveMessage(`Saved run ${saved.id} to ${projectId}.`);
    } catch (err) {
      setSaveMessage(err instanceof Error ? err.message : 'Could not save this run.');
    }
  }

  async function onSaveSuiteRuns() {
    if (!suiteSimulation) return;
    if (!userId) {
      setSaveMessage('Sign up first to save suite runs and project history.');
      return;
    }

    try {
      const saved = await Promise.all(
        suiteSimulation.scenario_runs.map((run) => saveBenchmarkRun({
          user_id: userId,
          project_id: projectId,
          plan,
          report: run.benchmark_report,
          transcript: run.transcript,
        })),
      );
      setSavedRuns((current) => [
        ...saved,
        ...current.filter((run) => !saved.some((savedRun) => savedRun.id === run.id)),
      ]);
      fetchProjectRegressionSummary(userId, projectId)
        .then(setProjectRegressionSummary)
        .catch(() => setProjectRegressionSummary(null));
      if (report?.suite_id && report.scenario_id) {
        fetchProjectRegressionSummary(userId, projectId, report.suite_id, report.scenario_id)
          .then(setScenarioRegressionSummary)
          .catch(() => setScenarioRegressionSummary(null));
      }
      listBenchmarkSuiteRuns(userId, projectId, suiteSimulation.suite_id)
        .then(setSuiteRuns)
        .catch(() => setSuiteRuns([]));
      await refreshAuditTrail();
      setSaveMessage(`Saved ${saved.length} suite runs to ${projectId}.`);
    } catch (err) {
      setSaveMessage(err instanceof Error ? err.message : 'Could not save this suite.');
    }
  }

  async function onJudge() {
    if (!report) return;
    try {
      setJudgeGate(await requestJudge({ plan, report, transcript, user_id: userId || undefined, project_id: userId ? projectId : undefined }));
      await refreshAuditTrail();
    } catch (err) {
      setJudgeGate({
        status: 'blocked',
        required_plan: 'starter',
        credits: 10,
        message: err instanceof Error ? err.message : 'Judge request failed.',
        evidence_citations: [],
      });
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
      setExportMessage(`Exported ${exported.suite_run_count} suite runs to ${exported.filename}. ${suiteHistoryExportSummary(exported.summary)} ${projectVconExportSummary(exported.vcon_export_summary)} ${projectContractArtifactSummary(exported.suite_contract_artifact_summary)}`);
    } catch (err) {
      setExportMessage(err instanceof Error ? err.message : 'Could not export suite run history.');
    }
  }

  async function onRefreshSuiteRuns() {
    if (!userId || !selectedSuite?.id) return;

    setIsRefreshingSuiteRuns(true);
    try {
      const refreshed = await listBenchmarkSuiteRuns(userId, projectId, selectedSuite.id, suiteRunStatusFilter);
      setSuiteRuns(refreshed);
      setSaveMessage(`Refreshed ${refreshed.length} suite runs for ${selectedSuite.title}.`);
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

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedSuite || !selectedScenario) return;

    setIsRunning(true);
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
      const nextReport = await runBenchmark({
        suite_id: selectedSuite.id,
        scenario_id: selectedScenario.id,
        transcript,
        final_state: parseMaybeJson(finalState),
        action_trace: parseMaybeJson(actionTrace),
        call: callEvidence.trim() ? parseMaybeJson(callEvidence) : undefined,
        group_call: groupCall.trim() ? parseMaybeJson(groupCall) : undefined,
        vcon: vconEvidence.trim() ? parseMaybeJson(vconEvidence) as JsonRecord : undefined,
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
          .then(setSuiteRuns)
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
    if (!userId) {
      setSaveMessage('Sign up first to queue retained suite runs.');
      return;
    }

    setIsEnqueueingSuite(true);
    setRunError(null);
    setCopyMessage(null);

    try {
      const runMetadata = cleanRunMetadata({
        agent_version: agentVersion,
        prompt_version: promptVersion,
        model_name: modelName,
        notes: runNotes,
        user_id: userId,
        project_id: projectId || undefined,
      });
      const queued = await enqueueBenchmarkSuiteSimulation({
        suite_id: selectedSuite.id,
        agent_profile: agentProfile,
        include_failure: includeFailure,
        ...runMetadata,
      });
      setSuiteRuns((current) => [queued, ...current.filter((run) => run.suite_run_id !== queued.suite_run_id)]);
      if (suiteRunStatusFilter) setSuiteRunStatusFilter('');
      let latest = queued;
      for (let attempt = 0; attempt < 8 && isActiveSuiteRunStatus(latest.status); attempt += 1) {
        await delay(750);
        try {
          latest = await fetchBenchmarkSuiteRun(userId, queued.suite_run_id);
          setSuiteRuns((current) => [latest, ...current.filter((run) => run.suite_run_id !== latest.suite_run_id)]);
        } catch {
          break;
        }
      }
      setSaveMessage(
        isActiveSuiteRunStatus(latest.status)
          ? `Queued suite run ${queued.suite_run_id} for ${projectId}; it is ${latest.status}.`
          : `Suite run ${queued.suite_run_id} finished as ${latest.status}.`,
      );
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Could not queue suite simulation.');
    } finally {
      setIsEnqueueingSuite(false);
    }
  }

  const evidence = report?.evidence_spans ?? report?.evidence ?? [];
  const score = report?.score ?? report?.overall_score;
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
  const pricing = productConfig?.pricing ?? [];
  const deterministicRule = productConfig?.usage_rules.find((rule) => rule.id === 'deterministic_eval');
  const judgeRule = productConfig?.usage_rules.find((rule) => rule.id === 'llm_judge');
  const voiceRule = productConfig?.usage_rules.find((rule) => rule.id === 'voice_webrtc_minute');
  const hasRunnableEvidence = Boolean(
    transcript.trim() || actionTrace.trim() || finalState.trim() || callEvidence.trim() || groupCall.trim() || vconEvidence.trim(),
  );
  const hasSavedCurrentScenario = Boolean(
    selectedScenario?.id && savedRuns.some((run) => run.report.scenario_id === selectedScenario.id),
  );
  const currentRegressionDelta = useMemo(() => currentReportRegressionDelta(report, savedRuns), [report, savedRuns]);
  const reportBrief = report ? formatReportBrief(report, selectedScenario?.title, currentRegressionDelta) : '';
  const actionPlan = report ? reportActionPlan(report, currentRegressionDelta) : null;
  const onboardingSteps = [
    {
      title: 'Pick a scenario',
      detail: selectedScenario ? selectedScenario.title : 'Choose the benchmark suite and scenario to test.',
      done: Boolean(selectedScenario),
      ready: Boolean(selectedScenario),
    },
    {
      title: 'Run evidence check',
      detail: report ? `Latest verdict: ${verdict ?? 'complete'}${score !== undefined ? ` at ${score}` : ''}.` : 'Simulate the scenario or run the benchmark against pasted evidence.',
      done: Boolean(report),
      ready: hasRunnableEvidence && !isRunning && !isSimulating,
    },
    {
      title: 'Save repeatable history',
      detail: hasSavedCurrentScenario
        ? `Focused history is tracking ${selectedScenario?.title ?? 'this scenario'}.`
        : userId
          ? 'Save the result to compare future prompt, model, and agent changes.'
          : 'Sign up with the demo identity, then save the run for regression history.',
      done: hasSavedCurrentScenario,
      ready: Boolean(report && userId),
    },
  ];

  return (
    <section style={{ display: 'grid', gap: 20 }}>
      {productConfig ? (
        <section className="product-console" aria-label="Product plan and authentication controls">
          <div className="console-panel">
            <p className="eyebrow">Free browser eval</p>
            <h2>Run deterministic checks now. Save and judge after signup.</h2>
            <p>
              This path is real: the browser sends transcript, action trace, and final state evidence to the benchmark API.
              Paid gates control persistence, LLM judging, CI/API, and voice minutes.
            </p>
            <div className="usage-strip">
              <span>{deterministicRule?.credits ?? 1} credit browser eval</span>
              <span>{judgeRule?.credits ?? 10} credits LLM judge</span>
              <span>{voiceRule?.credits ?? 5} credits voice minute</span>
            </div>
          </div>

          <div className="auth-panel">
            <div>
              <p className="eyebrow">Auth</p>
              <h3>{userId ? 'Signed in' : 'Firebase-ready signup'}</h3>
              <p>
                {productConfig.auth.mode === 'configured'
                  ? `Firebase project ${productConfig.auth.project_id} is configured.`
                  : 'Firebase providers are scaffolded; add project keys to use live auth.'}
              </p>
            </div>
            <label>
              <span>Project</span>
              <input
                value={projectId}
                onChange={(event) => {
                  setProjectId(event.target.value);
                  if (typeof window !== 'undefined') window.localStorage.setItem('conversation-evals-demo-project', event.target.value);
                }}
              />
            </label>
            <button type="button" className="primary-link" onClick={signInDemo}>
              {userId ? 'Refresh demo identity' : 'Sign up to save'}
            </button>
          </div>
        </section>
      ) : null}

      <section className="first-run-panel" aria-label="First run checklist">
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
                <div>
                  <strong>{step.title}</strong>
                  <p>{step.detail}</p>
                </div>
                <em aria-label={`${step.title}: ${status}`}>{status}</em>
              </li>
            );
          })}
        </ol>
      </section>

      {pricing.length ? (
        <section className="pricing-grid" aria-label="Pricing and upgrade gates">
          {pricing.map((item) => (
            <button
              type="button"
              className={`pricing-card ${plan === item.id ? 'selected' : ''}`}
              key={item.id}
              onClick={() => void onSelectPlan(item.id)}
            >
              <span>{item.name}</span>
              <strong>{item.price_label}</strong>
              <small>{item.seats}</small>
              <ul>
                {item.features.slice(0, 4).map((feature) => <li key={feature}>{feature}</li>)}
              </ul>
            </button>
          ))}
        </section>
      ) : null}

      {checkoutMessage ? (
        <p aria-live="polite" style={{ margin: 0, color: 'var(--muted)' }}>{checkoutMessage}</p>
      ) : null}

      <form onSubmit={onSubmit} className="card" style={{ padding: 24, display: 'grid', gap: 18 }}>
        {loadError ? (
          <div style={{ border: '1px solid var(--error-border)', background: 'var(--error-bg)', color: 'var(--error-text)', borderRadius: 8, padding: 12 }}>
            {loadError}
          </div>
        ) : null}

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
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
        </div>

        {isLoading ? <p style={{ margin: 0, color: 'var(--muted)' }}>Loading benchmark suites...</p> : null}

        {selectedScenario ? (
          <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16, background: 'var(--panel-alt)', display: 'grid', gap: 10 }}>
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
        ) : !isLoading ? (
          <p style={{ margin: 0, color: 'var(--muted)' }}>No benchmark scenarios are available yet.</p>
        ) : null}

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

        <details>
          <summary style={{ cursor: 'pointer', fontWeight: 800 }}>Evidence payload</summary>
          <div style={{ display: 'grid', gap: 16, marginTop: 14 }}>
            <label style={{ display: 'grid', gap: 8 }}>
              <span style={{ fontWeight: 700 }}>Transcript</span>
              <textarea
                value={transcript}
                onChange={(event) => setTranscript(event.target.value)}
                rows={7}
                style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }}
              />
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
              <label style={{ display: 'grid', gap: 8 }}>
                <span style={{ fontWeight: 700 }}>Action/tool trace</span>
                <textarea
                  value={actionTrace}
                  onChange={(event) => setActionTrace(event.target.value)}
                  rows={7}
                  style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }}
                />
              </label>

              <label style={{ display: 'grid', gap: 8 }}>
                <span style={{ fontWeight: 700 }}>Final observed state</span>
                <textarea
                  value={finalState}
                  onChange={(event) => setFinalState(event.target.value)}
                  rows={7}
                  style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }}
                />
              </label>
            </div>

            <label style={{ display: 'grid', gap: 8 }}>
              <span style={{ fontWeight: 700 }}>Voice call evidence</span>
              <textarea
                value={callEvidence}
                onChange={(event) => setCallEvidence(event.target.value)}
                rows={7}
                placeholder='{"turns":[{"speaker":"Caller","body":"I need a human."},{"speaker":"Agent","body":"I created a ticket and escalated you."}],"metrics":{"durationMs":92000,"avgLatencyMs":340},"media":{"recordingUrl":"https://storage.example.test/calls/demo.wav","mimeType":"audio/wav"}}'
                style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }}
              />
            </label>

            <label style={{ display: 'grid', gap: 8 }}>
              <span style={{ fontWeight: 700 }}>Group call evidence</span>
              <textarea
                value={groupCall}
                onChange={(event) => setGroupCall(event.target.value)}
                rows={7}
                placeholder='{"messages":[{"speaker":"Patient","text":"I need a refill"}],"decisions":["Route to clinician review"],"commitments":["Send update by 5 PM"],"follow_up_actions":["Confirm pharmacy"]}'
                style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }}
              />
            </label>

            <label style={{ display: 'grid', gap: 8 }}>
              <span style={{ fontWeight: 700 }}>vCon record</span>
              <textarea
                value={vconEvidence}
                onChange={(event) => setVconEvidence(event.target.value)}
                rows={7}
                placeholder='{"vcon":"0.0.1","parties":[{"name":"Caller"},{"name":"Agent"}],"dialog":[{"party":0,"body":"I need a human."},{"party":1,"body":"I created a ticket and escalated you."}]}'
                style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, resize: 'vertical', lineHeight: 1.45 }}
              />
            </label>
          </div>
        </details>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          <button
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
          </button>
          <button
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
          </button>
          <button
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
            {isEnqueueingSuite ? 'Queueing suite...' : 'Queue suite run'}
          </button>
          <button
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
            {isRunning ? 'Running benchmark...' : 'Run benchmark'}
          </button>
          <button
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
          </button>
          <button
            type="button"
            disabled={!report}
            onClick={onJudge}
            style={{
              border: '1px solid var(--border)',
              borderRadius: 8,
              background: plan === 'free' ? 'var(--panel-alt)' : 'white',
              color: 'var(--text)',
              padding: '12px 18px',
              fontWeight: 800,
              opacity: report ? 1 : 0.65,
            }}
          >
            Request LLM judge
          </button>
        </div>

        {runError ? <p style={{ color: 'var(--error-text)', margin: 0 }}>{runError}</p> : null}
        {saveMessage ? <p style={{ color: 'var(--muted)', margin: 0 }}>{saveMessage}</p> : null}
        {judgeGate ? (
          <div
            style={{
              border: `1px solid ${judgeGate.status === 'ready' ? 'var(--success-border)' : 'var(--error-border)'}`,
              background: judgeGate.status === 'ready' ? 'var(--success-bg)' : 'var(--error-bg)',
              color: judgeGate.status === 'ready' ? 'var(--success-text)' : 'var(--error-text)',
              borderRadius: 8,
              padding: 12,
            }}
          >
            <strong>{judgeGate.status === 'ready' ? 'Judge gate ready' : 'Upgrade required'}:</strong> {judgeGate.message}
            {formatJudgeSpend(judgeGate.spend_control) ? (
              <p style={{ margin: '8px 0 0', color: 'inherit' }}>{formatJudgeSpend(judgeGate.spend_control)}</p>
            ) : null}
          </div>
        ) : null}
      </form>

      {suiteSimulation ? (
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
            <ScoreTile label="Task completion" score={report.task_completion_score} />
            <ScoreTile label="Required actions" score={report.required_action_score} />
            <ScoreTile label="Forbidden actions" score={report.forbidden_action_score} />
            <ScoreTile label="Final state" score={report.final_state_score} />
          </div>

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
              </div>
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

          <details>
            <summary style={{ cursor: 'pointer', fontWeight: 800 }}>Raw benchmark report</summary>
            <pre style={{ overflowX: 'auto', background: '#0f172a', color: '#e2e8f0', borderRadius: 8, padding: 16 }}>
              {JSON.stringify(report, null, 2)}
            </pre>
          </details>
        </section>
      ) : null}

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

        <div className="card" style={{ padding: 20, display: 'grid', gap: 12 }}>
          <p className="eyebrow">Voice path</p>
          <h3 style={{ margin: 0 }}>Team-gated WebRTC evals</h3>
          <p style={{ margin: 0, color: 'var(--muted)' }}>
            Voice minutes are modeled in credits now. The visible gate keeps the product honest while the WebRTC/SIP runner is wired to real call evidence.
          </p>
        </div>
      </section>
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

function ScoreTile({ label, score }: { label: string; score?: number }) {
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 14 }}>
      <p style={{ margin: '0 0 6px', color: 'var(--muted)', fontSize: 13 }}>{label}</p>
      <p style={{ margin: 0, fontSize: 24, fontWeight: 900, color: scoreColor(score) }}>{score ?? 'n/a'}</p>
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

  const signalCount = (summary.interruption_signal_count ?? 0) + (summary.correction_signal_count ?? 0);
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
