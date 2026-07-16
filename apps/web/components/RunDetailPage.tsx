'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { SiteNav } from '@/components/SiteNav';
import {
  ConversationRecord,
  demoProjectId,
  demoUserId,
  ExecutionRunRecord,
  getExecutionRun,
  TimelineEvent,
} from '@/lib/execution';

type MetricKey = 'audio_interruption' | 'latency' | 'call_resolution';

const METRICS: Array<{ id: MetricKey; group: string; label: string }> = [
  { id: 'audio_interruption', group: 'Audio', label: 'Interruption Detection' },
  { id: 'latency', group: 'Audio', label: 'Latency' },
  { id: 'call_resolution', group: 'Other', label: 'Call Resolution Success' },
];

export function RunDetailPage({ executionRunId }: { executionRunId: string }) {
  const [run, setRun] = useState<ExecutionRunRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metric, setMetric] = useState<MetricKey>('latency');
  const [conversationId, setConversationId] = useState('');
  const userId = useMemo(() => demoUserId(), []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setInterval> | undefined;

    async function load() {
      setError(null);
      try {
        const next = await getExecutionRun(userId, executionRunId);
        if (!active) return;
        setRun(next);
        setConversationId((current) => current || next.conversations?.[0]?.conversation_id || '');
        return next.status;
      } catch (err) {
        if (!active) return undefined;
        setError(err instanceof Error ? err.message : 'Could not load run');
        return undefined;
      }
    }

    void load().then((status) => {
      if (!active) return;
      if (status === 'queued' || status === 'running') {
        timer = setInterval(() => {
          void load().then((nextStatus) => {
            if (!active) return;
            if (nextStatus !== 'queued' && nextStatus !== 'running' && timer) {
              clearInterval(timer);
              timer = undefined;
            }
          });
        }, 1500);
      }
    });

    return () => {
      active = false;
      if (timer) clearInterval(timer);
    };
  }, [executionRunId, userId]);

  const conversation = useMemo(
    () => run?.conversations.find((item) => item.conversation_id === conversationId) || run?.conversations[0] || null,
    [conversationId, run],
  );

  const summary = useMemo(() => aggregateRunMetrics(run), [run]);

  return (
    <main className="page-shell runs-analysis-shell">
      <SiteNav current="runs" />
      <section className="minimal-hero" aria-labelledby="run-title">
        <p className="eyebrow">Run analysis</p>
        <h1 id="run-title">{run?.agent_name || run?.agent_id || executionRunId}</h1>
        <p>
          <Link href="/runs">All runs</Link>
          {' · '}
          {executionRunId}
          {run ? ` · ${run.status}` : ''}
        </p>
      </section>

      {error ? <div className="scenarios-error" role="alert">{error}</div> : null}
      {!run && !error ? <p className="scenarios-muted">Loading run…</p> : null}

      {run ? (
        <>
          <section className="metric-summary-grid" aria-label="Metric summaries">
            <MetricTile
              title="Interruption Detection"
              value={`${summary.interruptionCount}`}
              detail="fixture count across conversations"
              selected={metric === 'audio_interruption'}
              onClick={() => setMetric('audio_interruption')}
            />
            <MetricTile
              title="Latency"
              value={summary.avgLatency != null ? `${Math.round(summary.avgLatency)}ms` : 'n/a'}
              detail={`p90 ${summary.p90Latency != null ? `${Math.round(summary.p90Latency)}ms` : 'n/a'}`}
              bars={summary.latencyBars}
              selected={metric === 'latency'}
              onClick={() => setMetric('latency')}
            />
            <MetricTile
              title="Call Resolution Success"
              value={`${Math.round(summary.resolutionRate)}%`}
              detail="pass verdict = 100"
              selected={metric === 'call_resolution'}
              onClick={() => setMetric('call_resolution')}
            />
          </section>

          <div className="runs-analysis-layout">
            <aside className="card runs-metric-nav" aria-label="Metric navigation">
              {['Audio', 'Other'].map((group) => (
                <div key={group} className="runs-metric-group">
                  <p className="eyebrow">{group}</p>
                  {METRICS.filter((item) => item.group === group).map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      className={metric === item.id ? 'is-active' : undefined}
                      onClick={() => setMetric(item.id)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              ))}
              <label className="runs-conversation-picker">
                <span>Conversation</span>
                <select
                  aria-label="Conversation"
                  value={conversation?.conversation_id || ''}
                  onChange={(event) => setConversationId(event.target.value)}
                >
                  {(run.conversations || []).map((item) => (
                    <option key={item.conversation_id} value={item.conversation_id}>
                      {item.scenario_title || item.scenario_id}
                    </option>
                  ))}
                </select>
              </label>
            </aside>

            <section className="card runs-metric-detail" aria-label="Metric detail">
              <MetricDetail metric={metric} conversation={conversation} />
              {run.mode === 'voice_fixture' ? (
                <StubWaveform timeline={conversation?.timeline || []} />
              ) : null}
            </section>

            <section className="card runs-transcript" aria-label="Transcript">
              <p className="eyebrow">Transcript</p>
              <h2>{conversation?.scenario_title || conversation?.scenario_id || 'Conversation'}</h2>
              {(conversation?.turns || []).length ? (
                <ol>
                  {(conversation?.turns || []).map((turn) => (
                    <li key={`${turn.turn_index}-${turn.speaker}`}>
                      <strong>{turn.speaker || 'speaker'}</strong>
                      <p>{turn.text || '—'}</p>
                    </li>
                  ))}
                </ol>
              ) : (
                <pre>{conversation?.transcript || 'No transcript available.'}</pre>
              )}
            </section>
          </div>
        </>
      ) : null}
    </main>
  );
}

function MetricTile({
  title,
  value,
  detail,
  bars,
  selected,
  onClick,
}: {
  title: string;
  value: string;
  detail: string;
  bars?: number[];
  selected?: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" className={`metric-tile${selected ? ' is-selected' : ''}`} onClick={onClick}>
      <span>{title}</span>
      <strong>{value}</strong>
      <em>{detail}</em>
      {bars?.length ? (
        <span className="metric-mini-bars" aria-hidden="true">
          {bars.map((height, index) => (
            <i key={`${index}-${height}`} style={{ height: `${Math.max(8, height)}%` }} />
          ))}
        </span>
      ) : null}
    </button>
  );
}

function MetricDetail({
  metric,
  conversation,
}: {
  metric: MetricKey;
  conversation: ConversationRecord | null;
}) {
  const summary = conversation?.metrics_summary;
  if (!conversation || !summary) {
    return <p className="scenarios-muted">Select a conversation to inspect metrics.</p>;
  }

  if (metric === 'audio_interruption') {
    return (
      <div className="runs-detail-copy">
        <h2>Interruption Detection</h2>
        <p>{summary.interruption_count} interruption signals in this conversation (fixture-derived).</p>
      </div>
    );
  }

  if (metric === 'call_resolution') {
    return (
      <div className="runs-detail-copy">
        <h2>Call Resolution Success</h2>
        <p>
          Verdict <strong>{summary.verdict || 'n/a'}</strong> → {summary.call_resolution_success}% success
          (pass = 100, otherwise 0).
        </p>
      </div>
    );
  }

  const latency = summary.latency;
  const marks = conversation.latency_marks || [];
  return (
    <div className="runs-detail-copy">
      <h2>Latency</h2>
      <p>
        count {latency.count} · avg {fmtMs(latency.avg_ms)} · median {fmtMs(latency.median_ms)} · p90 {fmtMs(latency.p90_ms)} ·
        outliers {latency.outlier_count}
      </p>
      <div className="latency-bars" aria-label="Per-mark latency bars">
        {(marks.length ? marks : conversation.timeline || []).map((mark, index) => {
          const ms = markLatencyMs(mark);
          const max = Math.max(latency.max_ms || 1, 1);
          return (
            <div key={index} className="latency-bar-row">
              <span>{String((mark as { label?: string }).label || `mark ${index + 1}`)}</span>
              <span className="latency-bar-track">
                <i style={{ width: `${Math.min(100, (ms / max) * 100)}%` }} />
              </span>
              <strong>{fmtMs(ms)}</strong>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StubWaveform({ timeline }: { timeline: TimelineEvent[] }) {
  const events = timeline.length
    ? timeline
    : [
        { t_ms: 0, label: 'caller', latency_ms: 500, kind: 'turn' },
        { t_ms: 500, label: 'agent', latency_ms: 700, kind: 'turn' },
      ];
  const total = Math.max(
    1,
    ...events.map((item) => Number(item.t_ms || 0) + Number(item.latency_ms || 400)),
  );

  return (
    <div className="stub-waveform" aria-label="Stub dual-track waveform">
      <p className="eyebrow">Waveform (stub)</p>
      <div className="stub-track" data-track="caller">
        {events.map((event, index) => (
          <span
            key={`caller-${index}`}
            title={event.label}
            style={{
              left: `${(Number(event.t_ms || 0) / total) * 100}%`,
              width: `${(Number(event.latency_ms || 400) / total) * 100}%`,
              opacity: event.kind === 'turn' && index % 2 === 0 ? 0.9 : 0.35,
            }}
          />
        ))}
      </div>
      <div className="stub-track" data-track="agent">
        {events.map((event, index) => (
          <span
            key={`agent-${index}`}
            title={event.label}
            style={{
              left: `${(Number(event.t_ms || 0) / total) * 100}%`,
              width: `${(Number(event.latency_ms || 400) / total) * 100}%`,
              opacity: event.kind === 'turn' && index % 2 === 1 ? 0.9 : 0.35,
            }}
          />
        ))}
      </div>
    </div>
  );
}

function aggregateRunMetrics(run: ExecutionRunRecord | null) {
  const conversations = run?.conversations || [];
  const interruptionCount = conversations.reduce(
    (sum, item) => sum + Number(item.metrics_summary?.interruption_count || 0),
    0,
  );
  const latencies = conversations
    .map((item) => item.metrics_summary?.latency?.avg_ms)
    .filter((value): value is number => typeof value === 'number');
  const p90s = conversations
    .map((item) => item.metrics_summary?.latency?.p90_ms)
    .filter((value): value is number => typeof value === 'number');
  const resolutions = conversations.map((item) => Number(item.metrics_summary?.call_resolution_success || 0));
  const latencyBars = (conversations[0]?.timeline || [])
    .slice(0, 12)
    .map((item) => Math.min(100, (markLatencyMs(item) / 1000) * 100));
  return {
    interruptionCount,
    avgLatency: latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : null,
    p90Latency: p90s.length ? Math.max(...p90s) : null,
    resolutionRate: resolutions.length ? resolutions.reduce((a, b) => a + b, 0) / resolutions.length : 0,
    latencyBars,
  };
}

function markLatencyMs(mark: unknown): number {
  if (!mark || typeof mark !== 'object') return 0;
  const record = mark as Record<string, unknown>;
  for (const key of ['latency_ms', 'elapsed_ms', 'ms', 'duration_ms']) {
    const value = Number(record[key]);
    if (Number.isFinite(value) && value >= 0) return value;
  }
  return 0;
}

function fmtMs(value?: number | null) {
  if (value == null || Number.isNaN(value)) return 'n/a';
  return `${Math.round(value)}ms`;
}
