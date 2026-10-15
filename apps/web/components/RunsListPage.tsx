'use client';

import Link from 'next/link';
import dynamic from 'next/dynamic';
import { useEffect, useMemo, useState } from 'react';

import { ResultsPagination, RESULTS_PAGE_SIZE } from '@/components/ResultsPagination';
import { SiteNav } from '@/components/SiteNav';
import { ApiAwareLink } from '@/components/ApiAwareLink';
import {
  demoProjectId,
  demoUserId,
  ExecutionRunSummary,
  listExecutionRunSummaries,
} from '@/lib/execution';

const BenchmarkRunner = dynamic(() => import('@/components/BenchmarkRunner').then((module) => module.BenchmarkRunner), {
  loading: () => <p className="scenarios-muted" role="status">Loading evaluation tools…</p>,
});

function testerLabel(testerId?: ExecutionRunSummary['tester_id']) {
  if (testerId === 'fixture_replay') return 'saved conversation replay';
  if (testerId === 'pipecat_tester') return 'Pipecat voice tester';
  if (testerId === 'scenario_simulator') return 'scenario simulator';
  return null;
}

export function RunsListPage({ view = 'launch' }: { view?: 'launch' | 'results' }) {
  const [source, setSource] = useState<'agents' | 'saved'>('agents');
  const [runs, setRuns] = useState<ExecutionRunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [apiBase, setApiBase] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const userId = useMemo(() => demoUserId(), []);
  const projectId = useMemo(() => demoProjectId(), []);

  useEffect(() => {
    setApiBase(new URLSearchParams(window.location.search).get('api_base') || '');
  }, []);

  useEffect(() => {
    if (view !== 'results' || source !== 'agents') { setLoading(false); return; }
    let active = true;
    let pageTimer: ReturnType<typeof setTimeout> | undefined;
    let probeTimer: ReturnType<typeof setTimeout> | undefined;
    let pagePending = false;
    let probePending = false;
    let pageHasActiveRuns = false;
    let probeHasResolved = false;
    let finalRefresh = false;
    // Unknown presence is conservative until the first probe responds.
    let projectHasActiveRuns = true;
    const hasActive = (rows: ExecutionRunSummary[]) => rows.some((run) => run.status === 'queued' || run.status === 'running');

    function queuePage(delay = 1500) {
      if (!active || pagePending || pageTimer || (!pageHasActiveRuns && !projectHasActiveRuns && !finalRefresh)) return;
      pageTimer = setTimeout(() => { pageTimer = undefined; void loadPage(); }, delay);
    }

    function queueProbe(delay = 1500) {
      if (!active || probePending || probeTimer || (!pageHasActiveRuns && !projectHasActiveRuns)) return;
      probeTimer = setTimeout(() => { probeTimer = undefined; void probeActiveRuns(); }, delay);
    }

    async function loadPage(initial = false) {
      pagePending = true;
      const completingRefresh = finalRefresh;
      finalRefresh = false;
      let delay = 1500;
      if (initial) { setLoading(true); setError(null); }
      try {
        const next = await listExecutionRunSummaries(userId, projectId, { limit: RESULTS_PAGE_SIZE + 1, offset: page * RESULTS_PAGE_SIZE, status: statusFilter });
        if (!active) return;
        setRuns(next.slice(0, RESULTS_PAGE_SIZE));
        setHasNext(next.length > RESULTS_PAGE_SIZE);
        setError(null);
        pageHasActiveRuns = hasActive(next);
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : 'Could not load runs');
        if (completingRefresh) finalRefresh = true;
        delay = 3000;
      } finally {
        pagePending = false;
        if (active) {
          if (initial) setLoading(false);
          queuePage(delay);
          queueProbe();
        }
      }
    }

    async function probeActiveRuns() {
      probePending = true;
      let delay = 1500;
      try {
        const rows = await listExecutionRunSummaries(userId, projectId, { limit: 1, offset: 0, status: 'active' });
        if (!active) return;
        const nextHasActive = hasActive(rows);
        // A run outside this page can move into it when it completes. Fetch a
        // final snapshot after that transition, including if a page is in flight.
        if (probeHasResolved && projectHasActiveRuns && !nextHasActive) finalRefresh = true;
        projectHasActiveRuns = nextHasActive;
        probeHasResolved = true;
      } catch {
        if (!active) return;
        projectHasActiveRuns = true;
        delay = 3000;
      } finally {
        probePending = false;
        if (active) {
          if (!pageHasActiveRuns && !projectHasActiveRuns && !finalRefresh && pageTimer) {
            clearTimeout(pageTimer);
            pageTimer = undefined;
          }
          queuePage();
          queueProbe(delay);
        }
      }
    }

    // A slow/hung presence probe must not hold back rows or page refreshes.
    void loadPage(true);
    void probeActiveRuns();
    return () => {
      active = false;
      if (pageTimer) clearTimeout(pageTimer);
      if (probeTimer) clearTimeout(probeTimer);
    };
  }, [page, projectId, refreshKey, source, statusFilter, userId, view]);


  return (
    <main className={`page-shell compact-shell ${view === 'results' ? 'results-page' : ''}`}>
      <SiteNav current={view === 'results' ? 'results' : 'runs'} />
      <section className="minimal-hero" aria-labelledby="runs-title">
        <p className="eyebrow">{view === 'results' ? 'Results · evidence and history' : 'New evaluation · live agent'}</p>
        <h1 id="runs-title">{view === 'results' ? 'Results' : 'Test an agent'}</h1>
        <p>{view === 'results' ? 'Review test outcomes and the evidence behind them.' : 'Choose reviewed cases and a text or voice agent. This starts a new execution and captures evidence for evaluation.'}</p>
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

      </>}

      {view === 'results' && source === 'agents' ? <>
      {loading ? <p className="scenarios-muted">Loading runs…</p> : null}
      {error ? <div className="scenarios-error" role="alert">{error} <button type="button" onClick={() => setRefreshKey((key) => key + 1)}>Retry</button></div> : null}

      {!loading && !error && !runs.length && statusFilter === 'all' && page === 0 ? (
        <section className="card scenarios-empty">
          <h2>No execution runs yet</h2>
          <p className="scenarios-muted">Choose a test set and agent to capture your first execution.</p>
          <ApiAwareLink className="secondary-link" href="/runs">Test an agent</ApiAwareLink>
        </section>
      ) : null}
      {(
      <section className="runs-history" aria-labelledby="recent-runs-title" aria-busy={loading}>
        <div className="runs-history-heading">
          <div>
            <h2 id="recent-runs-title">Agent tests</h2>
            <span>{runs.length ? `${page * RESULTS_PAGE_SIZE + 1}–${page * RESULTS_PAGE_SIZE + runs.length}` : '0'} shown · newest first</span>
          </div>
          <label>
            <span>Show</span>
            <select aria-label="Filter runs by status" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setPage(0); setRuns([]); setLoading(true); }}>
              <option value="all">All statuses</option>
              <option value="active">Active</option>
              <option value="completed">Completed</option>
              <option value="needs_review">Needs review</option>
              <option value="failed">Failed</option>
            </select>
          </label>
        </div>
        {!loading && !error && !runs.length && (statusFilter !== 'all' || page > 0) ? <p className="runs-filter-empty">No runs on this page. Choose another status or return to the previous page.</p> : null}
        <div className="runs-list">
        {runs.map((run) => {
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
                <p><time dateTime={run.updated_at}>{run.updated_at ? new Date(run.updated_at).toLocaleString() : 'Date unavailable'}</time> · {run.execution_run_id}</p>
                <p className="runs-list-meta">
                  {[testerLabel(run.tester_id), run.executor_id?.replaceAll('_', ' ')].filter(Boolean).join(' · ') || 'Execution record'}
                </p>
              </div>
              <div className="runs-list-secondary">
                <span>{run.conversations?.length || 0} {run.conversations?.length === 1 ? 'conversation' : 'conversations'}</span>
                <span>{avg != null ? `Score ${avg}` : 'Score pending'}</span>
              </div>
            </Link>
          );
        })}
        </div>
        <ResultsPagination page={page} hasNext={hasNext && !error} loading={loading} onChange={(next) => { setPage(next); setRuns([]); setLoading(true); }} label="Agent tests" />
      </section>
      )}
      </> : null}
      {view === 'results' ? <details className="results-advanced"><summary>Advanced tools</summary><ApiAwareLink href="/benchmarks">Benchmark console and exports</ApiAwareLink> · <ApiAwareLink href="/voice">Voice quick start</ApiAwareLink></details> : null}
    </main>
  );
}
