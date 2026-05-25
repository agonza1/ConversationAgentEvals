'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';

type JsonRecord = Record<string, unknown>;

interface BenchmarkSuite {
  id: string;
  title: string;
  description?: string | null;
  scenarios: BenchmarkScenario[];
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
  verdict?: string;
  overall?: string;
  scenario_title?: string;
  score?: number;
  overall_score?: number;
  task_completion_score?: number;
  required_action_score?: number;
  forbidden_action_score?: number;
  final_state_score?: number;
  evidence_spans?: Array<string | JsonRecord>;
  evidence?: Array<string | JsonRecord>;
  missing_actions?: string[];
  forbidden_actions_observed?: string[];
  failure_categories?: string[];
  suggested_fixes?: string[];
  transcript?: string;
  action_trace?: unknown;
  final_state?: unknown;
  run_metadata?: RunMetadata;
  evidence_audit_summary?: EvidenceAuditSummary;
}

interface RunMetadata {
  agent_version?: string;
  prompt_version?: string;
  model_name?: string;
  notes?: string;
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

interface SavedRun {
  id: string;
  project_id: string;
  plan: PricingPlan['id'];
  report: BenchmarkReport;
  transcript?: string | null;
  created_at: string;
}

interface SavedRunExport {
  id: string;
  filename: string;
  project_id: string;
  report: BenchmarkReport;
  transcript?: string | null;
  created_at: string;
}

interface JudgeGate {
  status: 'blocked' | 'ready';
  required_plan: PricingPlan['id'];
  credits: number;
  message: string;
  evidence_citations: string[];
}

interface BenchmarkSimulationResponse {
  transcript: string;
  action_trace: unknown;
  final_state: unknown;
  benchmark_report: BenchmarkReport;
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

async function runBenchmark(payload: {
  suite_id: string;
  scenario_id: string;
  transcript: string;
  action_trace: string | JsonRecord | unknown[];
  final_state: string | JsonRecord | unknown[];
  agent_version?: string;
  prompt_version?: string;
  model_name?: string;
  notes?: string;
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
}) {
  return handleJson<BenchmarkSimulationResponse>(
    await fetch(`${getApiBase()}/api/benchmarks/simulate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

async function fetchProductConfig(): Promise<ProductConfig> {
  return handleJson<ProductConfig>(await fetch(`${getApiBase()}/api/product/config`, { cache: 'no-store' }));
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

async function listSavedRuns(userId: string, projectId: string) {
  return handleJson<SavedRun[]>(
    await fetch(`${getApiBase()}/api/product/runs?user_id=${encodeURIComponent(userId)}&project_id=${encodeURIComponent(projectId)}`, { cache: 'no-store' }),
  );
}

async function exportSavedRun(userId: string, runId: string) {
  return handleJson<SavedRunExport>(
    await fetch(`${getApiBase()}/api/product/runs/${encodeURIComponent(runId)}/export?user_id=${encodeURIComponent(userId)}`, { cache: 'no-store' }),
  );
}

async function requestJudge(payload: { plan: PricingPlan['id']; report: BenchmarkReport; transcript: string }) {
  return handleJson<JudgeGate>(
    await fetch(`${getApiBase()}/api/product/judge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

function scoreColor(score: number | undefined) {
  if (score === undefined) return 'var(--muted)';
  if (score >= 80) return 'var(--success-text)';
  if (score >= 60) return '#b45309';
  return 'var(--danger)';
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

export function BenchmarkRunner() {
  const [suites, setSuites] = useState<BenchmarkSuite[]>([]);
  const [productConfig, setProductConfig] = useState<ProductConfig | null>(null);
  const [selectedSuiteId, setSelectedSuiteId] = useState('');
  const [selectedScenarioId, setSelectedScenarioId] = useState('');
  const [transcript, setTranscript] = useState('');
  const [actionTrace, setActionTrace] = useState('');
  const [finalState, setFinalState] = useState('');
  const [agentProfile, setAgentProfile] = useState('mock text agent');
  const [agentVersion, setAgentVersion] = useState('');
  const [promptVersion, setPromptVersion] = useState('');
  const [modelName, setModelName] = useState('');
  const [runNotes, setRunNotes] = useState('');
  const [includeFailure, setIncludeFailure] = useState(false);
  const [report, setReport] = useState<BenchmarkReport | null>(null);
  const [userId, setUserId] = useState('');
  const [projectId, setProjectId] = useState('call-center-demo');
  const [plan, setPlan] = useState<PricingPlan['id']>('free');
  const [savedRuns, setSavedRuns] = useState<SavedRun[]>([]);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [exportMessage, setExportMessage] = useState<string | null>(null);
  const [judgeGate, setJudgeGate] = useState<JudgeGate | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [isSimulating, setIsSimulating] = useState(false);

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
      return;
    }

    let isMounted = true;
    listSavedRuns(userId, projectId)
      .then((runs) => {
        if (isMounted) setSavedRuns(runs);
      })
      .catch(() => {
        if (isMounted) setSavedRuns([]);
      });

    return () => {
      isMounted = false;
    };
  }, [projectId, userId]);

  useEffect(() => {
    if (!selectedSuite) return;
    setSelectedScenarioId((current) => (
      selectedSuite.scenarios.some((scenario) => scenario.id === current) ? current : selectedSuite.scenarios[0]?.id ?? ''
    ));
  }, [selectedSuite]);

  useEffect(() => {
    if (!selectedScenario) return;

    setTranscript(selectedScenario.sample_transcript ?? '');
    setActionTrace(stringifyEditable(selectedScenario.sample_action_trace, '[]'));
    setFinalState(stringifyEditable(selectedScenario.sample_final_state ?? selectedScenario.expected_final_state, '{}'));
    setReport(null);
    setSaveMessage(null);
    setJudgeGate(null);
    setRunError(null);
  }, [selectedScenario]);

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

  async function onSaveRun() {
    if (!report) return;
    if (!userId) {
      setSaveMessage('Sign up first to save projects and run history.');
      return;
    }

    try {
      const saved = await saveBenchmarkRun({ user_id: userId, project_id: projectId, plan, report, transcript });
      setSavedRuns((current) => [saved, ...current.filter((run) => run.id !== saved.id)]);
      setSaveMessage(`Saved run ${saved.id} to ${projectId}.`);
    } catch (err) {
      setSaveMessage(err instanceof Error ? err.message : 'Could not save this run.');
    }
  }

  async function onJudge() {
    if (!report) return;
    try {
      setJudgeGate(await requestJudge({ plan, report, transcript }));
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
      const blob = new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' });
      const href = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = exported.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(href);
      setExportMessage(`Exported ${exported.filename}.`);
    } catch (err) {
      setExportMessage(err instanceof Error ? err.message : 'Could not export this saved run.');
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedSuite || !selectedScenario) return;

    setIsRunning(true);
    setRunError(null);
    setReport(null);

    try {
      const runMetadata = cleanRunMetadata({
        agent_version: agentVersion,
        prompt_version: promptVersion,
        model_name: modelName,
        notes: runNotes,
      });
      const nextReport = await runBenchmark({
        suite_id: selectedSuite.id,
        scenario_id: selectedScenario.id,
        transcript,
        action_trace: parseMaybeJson(actionTrace),
        final_state: parseMaybeJson(finalState),
        ...runMetadata,
      });
      setReport(nextReport);
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

    try {
      const runMetadata = cleanRunMetadata({
        agent_version: agentVersion,
        prompt_version: promptVersion,
        model_name: modelName,
        notes: runNotes,
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
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Scenario simulation failed');
    } finally {
      setIsSimulating(false);
    }
  }

  const evidence = report?.evidence_spans ?? report?.evidence ?? [];
  const score = report?.score ?? report?.overall_score;
  const verdict = report?.verdict ?? report?.overall;
  const pricing = productConfig?.pricing ?? [];
  const deterministicRule = productConfig?.usage_rules.find((rule) => rule.id === 'deterministic_eval');
  const judgeRule = productConfig?.usage_rules.find((rule) => rule.id === 'llm_judge');
  const voiceRule = productConfig?.usage_rules.find((rule) => rule.id === 'voice_webrtc_minute');

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

      {pricing.length ? (
        <section className="pricing-grid" aria-label="Pricing and upgrade gates">
          {pricing.map((item) => (
            <button
              type="button"
              className={`pricing-card ${plan === item.id ? 'selected' : ''}`}
              key={item.id}
              onClick={() => updatePlan(item.id)}
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
            type="submit"
            disabled={isRunning || isSimulating || !selectedScenario || !transcript.trim()}
            style={{
              border: 0,
              borderRadius: 8,
              background: 'var(--accent)',
              color: 'white',
              padding: '12px 18px',
              fontWeight: 800,
              opacity: isRunning || isSimulating || !selectedScenario || !transcript.trim() ? 0.65 : 1,
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
          </div>
        ) : null}
      </form>

      {report ? (
        <section className="card" style={{ padding: 24, display: 'grid', gap: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
            <div>
              <p style={{ margin: '0 0 6px', color: 'var(--muted)' }}>Benchmark report</p>
              <h2 style={{ margin: 0, fontSize: 28, textTransform: 'capitalize' }}>{verdict ?? 'Complete'}</h2>
            </div>
            {score !== undefined ? (
              <div style={{ fontSize: 40, fontWeight: 900, color: scoreColor(score) }}>{score}</div>
            ) : null}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 12 }}>
            <ScoreTile label="Task completion" score={report.task_completion_score} />
            <ScoreTile label="Required actions" score={report.required_action_score} />
            <ScoreTile label="Forbidden actions" score={report.forbidden_action_score} />
            <ScoreTile label="Final state" score={report.final_state_score} />
          </div>

          <RunMetadataPanel metadata={report.run_metadata} />
          <EvidenceAuditPanel summary={report.evidence_audit_summary} />

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
          <h3 style={{ margin: 0 }}>{userId ? `${savedRuns.length} saved for ${projectId}` : 'Signup required'}</h3>
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
                  <button
                    type="button"
                    onClick={() => void onExportRun(run.id)}
                    style={{
                      marginLeft: 8,
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
                </li>
              ))}
            </ul>
          ) : (
            <p style={{ margin: 0, color: 'var(--muted)' }}>Run a benchmark, sign up, then save it to build project history.</p>
          )}
          {exportMessage ? <p style={{ margin: 0, color: 'var(--muted)' }}>{exportMessage}</p> : null}
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
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10, background: 'var(--panel-alt)' }}>
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
