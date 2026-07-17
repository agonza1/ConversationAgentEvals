'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { SiteNav } from '@/components/SiteNav';
import { BenchmarkRunner } from '@/components/BenchmarkRunner';
import {
  demoProjectId,
  demoUserId,
  ExecutionRunRecord,
  listExecutionRuns,
} from '@/lib/execution';

export function RunsListPage() {
  const [runs, setRuns] = useState<ExecutionRunRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiBase, setApiBase] = useState('');
  const userId = useMemo(() => demoUserId(), []);
  const projectId = useMemo(() => demoProjectId(), []);

  useEffect(() => {
    setApiBase(new URLSearchParams(window.location.search).get('api_base') || '');
  }, []);

  useEffect(() => {
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
  }, [projectId, userId]);

  return (
    <main className="page-shell compact-shell">
      <SiteNav current="runs" />
      <section className="minimal-hero" aria-labelledby="runs-title">
        <p className="eyebrow">Run agent → analyze</p>
        <h1 id="runs-title">Run an agent</h1>
        <p>Launch a configured agent target, then review execution metrics, latency detail, and transcripts.</p>
      </section>

      <BenchmarkRunner view="run" />

      {loading ? <p className="scenarios-muted">Loading runs…</p> : null}
      {error ? <div className="scenarios-error" role="alert">{error}</div> : null}

      {!loading && !runs.length ? (
        <section className="card scenarios-empty">
          <h2>No execution runs yet</h2>
          <p className="scenarios-muted">Launch the selected agent above, or choose a different test from the scenario catalog.</p>
          <Link className="secondary-link" href="/scenarios">Browse scenarios</Link>
        </section>
      ) : null}

      <div className="runs-list">
        {runs.map((run) => {
          const scores = (run.conversations || [])
            .map((item) => item.score)
            .filter((value): value is number => typeof value === 'number');
          const avg = scores.length ? Math.round(scores.reduce((sum, value) => sum + value, 0) / scores.length) : null;
          const detailQuery = apiBase ? `?api_base=${encodeURIComponent(apiBase)}` : '';
          return (
            <Link key={run.execution_run_id} className="card runs-list-item" href={`/runs/${run.execution_run_id}${detailQuery}`}>
              <div>
                <strong>{run.agent_name || run.agent_id || run.mode}</strong>
                <p>{run.execution_run_id}</p>
              </div>
              <div className="runs-list-meta">
                <span data-status={run.status}>{run.status}</span>
                <span>{run.conversations?.length || 0} conversations</span>
                <span>{avg !== null ? `avg ${avg}` : 'n/a'}</span>
              </div>
            </Link>
          );
        })}
      </div>
    </main>
  );
}
