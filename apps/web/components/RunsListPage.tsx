'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { SiteNav } from '@/components/SiteNav';
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

export function RunsListPage() {
  const [runs, setRuns] = useState<ExecutionRunRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiBase, setApiBase] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [statusFilter, setStatusFilter] = useState('all');
  const hasRunsRef = useRef(false);
  const userId = useMemo(() => demoUserId(), []);
  const projectId = useMemo(() => demoProjectId(), []);

  useEffect(() => {
    setApiBase(new URLSearchParams(window.location.search).get('api_base') || '');
  }, []);

  useEffect(() => {
    let active = true;
    async function load() {
      // Keep existing rows visible while refreshing after a launch.
      if (!hasRunsRef.current) setLoading(true);
      setError(null);
      try {
        const next = await listExecutionRuns(userId, projectId);
        if (!active) return;
        setRuns(next);
        hasRunsRef.current = next.length > 0;
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
  }, [projectId, refreshKey, userId]);

  const hasActiveRuns = runs.some((run) => run.status === 'queued' || run.status === 'running');

  useEffect(() => {
    if (!hasActiveRuns) return undefined;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const next = await listExecutionRuns(userId, projectId);
        if (!active) return;
        setRuns(next);
        hasRunsRef.current = next.length > 0;
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
  }, [hasActiveRuns, projectId, userId]);

  const visibleRuns = useMemo(() => runs.filter((run) => {
    if (statusFilter === 'all') return true;
    if (statusFilter === 'active') return run.status === 'queued' || run.status === 'running';
    return run.status === statusFilter;
  }), [runs, statusFilter]);

  const upsertExecutionRun = useCallback((run: ExecutionRunRecord) => {
    setRuns((current) => {
      const next = [run, ...current.filter((item) => item.execution_run_id !== run.execution_run_id)];
      hasRunsRef.current = next.length > 0;
      return next;
    });
  }, []);

  function onExecutionCreated(run: ExecutionRunRecord) {
    upsertExecutionRun(run);
    setRefreshKey((value) => value + 1);
  }

  return (
    <main className="page-shell compact-shell">
      <SiteNav current="runs" />
      <section className="minimal-hero" aria-labelledby="runs-title">
        <p className="eyebrow">Run agent → analyze</p>
        <h1 id="runs-title">Run an agent</h1>
        <p>Launch a configured agent target, then review execution metrics, latency detail, and transcripts.</p>
      </section>

      <BenchmarkRunner
        view="run"
        onExecutionCreated={onExecutionCreated}
        onExecutionUpdated={upsertExecutionRun}
      />

      {loading ? <p className="scenarios-muted">Loading runs…</p> : null}
      {error ? <div className="scenarios-error" role="alert">{error}</div> : null}

      {!loading && !runs.length ? (
        <section className="card scenarios-empty">
          <h2>No execution runs yet</h2>
          <p className="scenarios-muted">Launch the selected agent above, or choose a different test from the scenario catalog.</p>
          <Link className="secondary-link" href="/scenarios">Browse scenarios</Link>
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
    </main>
  );
}
