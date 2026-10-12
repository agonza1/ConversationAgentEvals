'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { SiteNav } from '@/components/SiteNav';
import { ApiAwareLink } from '@/components/ApiAwareLink';
import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import {
  demoProjectId,
  demoUserId,
  ExecutionRunRecord,
  listExecutionRuns,
} from '@/lib/execution';

function testerLabel(testerId?: ExecutionRunRecord['tester_id']) {
  if (testerId === 'fixture_replay') return 'saved conversation replay';
  if (testerId === 'pipecat_tester') return 'Pipecat voice tester';
  if (testerId === 'scenario_simulator') return 'scenario simulator';
  return null;
}

export function RunsListPage({ view = 'launch' }: { view?: 'launch' | 'results' }) {
  const [source, setSource] = useState<'agents' | 'saved'>('agents');
  const [runs, setRuns] = useState<ExecutionRunRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiBase, setApiBase] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const userId = useMemo(() => demoUserId(), []);
  const projectId = useMemo(() => demoProjectId(), []);

  useEffect(() => {
    setApiBase(new URLSearchParams(window.location.search).get('api_base') || '');
  }, []);

  useEffect(() => {
    if (view !== 'results') { setLoading(false); return; }
    let active = true;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const next = await listExecutionRuns(userId, projectId);
        if (!active) return;
        setRuns(next);
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : 'Could not load runs');
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [projectId, userId, view]);

  const hasActiveRuns = runs.some((run) => run.status === 'queued' || run.status === 'running');

  useEffect(() => {
    if (view !== 'results' || !hasActiveRuns) return undefined;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const next = await listExecutionRuns(userId, projectId);
        if (!active) return;
        setRuns(next);
        setError(null);
        if (next.some((run) => run.status === 'queued' || run.status === 'running')) {
          timer = setTimeout(() => void poll(), 1500);
        }
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : 'Could not refresh active runs');
        timer = setTimeout(() => void poll(), 3000);
      }
    }

    timer = setTimeout(() => void poll(), 1500);
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [hasActiveRuns, projectId, userId, view]);

  const visibleRuns = useMemo(() => runs.filter((run) => {
    if (statusFilter === 'all') return true;
    if (statusFilter === 'active') return run.status === 'queued' || run.status === 'running';
    return run.status === statusFilter;
  }), [runs, statusFilter]);

  return (
    <main className="page-shell compact-shell">
      <SiteNav current={view === 'results' ? 'results' : 'runs'} />
      <section className="minimal-hero" aria-labelledby="runs-title">
        <p className="eyebrow">{view === 'results' ? 'Results · evidence and history' : 'New evaluation · live agent'}</p>
        <h1 id="runs-title">{view === 'results' ? 'Results' : 'Test an agent'}</h1>
        <p>{view === 'results' ? 'Review agent tests and saved evidence evaluations. Their source stays explicit; scores are not proof of missing actions.' : 'Choose reviewed cases and a text or voice agent. This starts a new execution and captures evidence for evaluation.'}</p>
      </section>

      {view === 'launch' ? <>
      <BenchmarkRunner view="run" />
      <ApiAwareLink href="/results">View results and history →</ApiAwareLink>
      </> : <>
        <div className="results-source-control" role="group" aria-label="Result source">
          <button type="button" className="secondary-link" aria-pressed={source === 'agents'} onClick={() => setSource('agents')}>Agent tests</button>
          <button type="button" className="secondary-link" aria-pressed={source === 'saved'} onClick={() => setSource('saved')}>Saved evaluations</button>
        </div>
        <p className="scenarios-muted">{source === 'agents' ? 'Executions captured by CAE, including live targets and explicitly labeled replays.' : 'Saved evaluations of existing evidence and benchmark suites; these records do not imply a new live call.'}</p>
        {source === 'saved' ? <BenchmarkRunner view="history" /> : null}
        <details><summary>Advanced tools</summary><ApiAwareLink href="/benchmarks">Open legacy benchmark console</ApiAwareLink> · <ApiAwareLink href="/voice">Voice quick start</ApiAwareLink></details>
      </>}

      {view === 'results' && source === 'agents' ? <>
      {loading ? <p className="scenarios-muted">Loading runs…</p> : null}
      {error ? <div className="scenarios-error" role="alert">{error}</div> : null}

      {!loading && !runs.length ? (
        <section className="card scenarios-empty">
          <h2>No execution runs yet</h2>
          <p className="scenarios-muted">Choose a test set and agent to capture your first execution.</p>
          <ApiAwareLink className="secondary-link" href="/runs">Test an agent</ApiAwareLink>
        </section>
      ) : null}
      {runs.length ? (
      <section className="runs-history" aria-labelledby="recent-runs-title">
        <div className="runs-history-heading">
          <div>
            <p className="eyebrow">Execution history</p>
            <h2 id="recent-runs-title">Recent runs</h2>
            <span>{runs.length} {runs.length === 1 ? 'run' : 'runs'} captured for this project</span>
          </div>
          <label>
            <span>Show</span>
            <select aria-label="Filter runs by status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="completed">Completed</option>
              <option value="needs_review">Needs review</option>
              <option value="failed">Failed</option>
            </select>
          </label>
        </div>
        {!visibleRuns.length ? <p className="runs-filter-empty">No runs match this filter.</p> : null}
        <div className="runs-list">
        {visibleRuns.map((run) => {
          const scores = (run.conversations || [])
            .map((item) => item.score)
            .filter((value): value is number => typeof value === 'number');
          const avg = scores.length ? Math.round(scores.reduce((sum, value) => sum + value, 0) / scores.length) : null;
          const detailQuery = apiBase ? `?api_base=${encodeURIComponent(apiBase)}` : '';
          return (
            <Link key={run.execution_run_id} className="runs-list-item" href={`/runs/${run.execution_run_id}${detailQuery}`}>
              <div className="runs-list-primary">
                <div className="runs-list-title">
                  <strong>{run.agent_name || run.agent_id || run.mode}</strong>
                  <span data-status={run.status}>{run.status.replaceAll('_', ' ')}</span>
                </div>
                <p>{run.execution_run_id}</p>
                <p className="runs-list-meta">
                  {[testerLabel(run.tester_id), run.executor_id?.replaceAll('_', ' ')].filter(Boolean).join(' · ') || 'Execution record'}
                </p>
              </div>
              <div className="runs-list-secondary">
                <span>{run.conversations?.length || 0} conversations</span>
                <span>{avg != null ? `avg ${avg}` : 'scoring pending'}</span>
              </div>
            </Link>
          );
        })}
        </div>
      </section>
      ) : null}
      </> : null}
    </main>
  );
}
