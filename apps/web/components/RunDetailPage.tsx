'use client';

import { useEffect, useMemo, useState } from 'react';

import { ApiAwareLink } from '@/components/ApiAwareLink';
import { LiveRunFeedback } from '@/components/LiveRunFeedback';
import { SiteNav } from '@/components/SiteNav';
import {
  ConversationRecord,
  ConversationTurn,
  demoProjectId,
  demoUserId,
  ExecutionRunRecord,
  getApiBase,
  getExecutionRun,
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
    let timer: ReturnType<typeof setTimeout> | undefined;

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

    async function poll() {
      const status = await load();
      if (!active) return;
      // A transient load failure returns undefined. Keep retrying so an active
      // analysis page cannot get stranded on a stale queued/running snapshot.
      if (status === undefined || status === 'queued' || status === 'running') {
        timer = setTimeout(() => void poll(), 1500);
      }
    }

    void poll();

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
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
        {run?.provenance?.honesty_label ? (
          <p className="runs-honesty-label" role="status">
            {run.provenance.honesty_label}
          </p>
        ) : null}
        {run?.provenance ? (
          <dl className="runs-provenance" aria-label="Run provenance">
            <div><dt>Target</dt><dd>{run.provenance.target_kind}</dd></div>
            <div><dt>Tester</dt><dd>{run.provenance.tester_id}</dd></div>
            <div><dt>Executor</dt><dd>{run.provenance.executor_id}</dd></div>
            <div><dt>Exchange cap</dt><dd>{run.max_exchanges || 3}</dd></div>
            <div><dt>Evidence</dt><dd>{run.provenance.evidence_source}</dd></div>
          </dl>
        ) : null}
        <p>
          <ApiAwareLink href="/runs">All runs</ApiAwareLink>
          {' · '}
          {executionRunId}
          {run ? ` · ${run.status}` : ''}
        </p>
      </section>

      {error ? <div className="scenarios-error" role="alert">{error}</div> : null}
      {!run && !error ? <p className="scenarios-muted">Loading run…</p> : null}

      {run ? (
        <>
          <section className="card run-participants" aria-label="Run participants and executor">
            <div>
              <span>Target under test</span>
              <strong>{run.agent_name || run.agent_id || 'Unattributed target'}</strong>
              <small>{runTargetSummary(run)}</small>
            </div>
            <div>
              <span>Tester</span>
              <strong>{formatTesterId(run.tester_id)}</strong>
              <small>Plays the scenario user/caller and supplies test turns.</small>
            </div>
            <div>
              <span>Executor</span>
              <strong>{formatRuntimeId(run.executor_id || 'local_async_runner')}</strong>
              <small>Invokes the adapter and persists evidence; it is not scored.</small>
            </div>
          </section>
          <section className="metric-summary-grid" aria-label="Metric summaries">
            <MetricTile
              title="Interruption Detection"
              value={`${summary.interruptionCount}`}
              detail="sample count across conversations"
              selected={metric === 'audio_interruption'}
              onClick={() => setMetric('audio_interruption')}
            />
            <MetricTile
              title={summary.targetSpecific ? 'Target Response Latency' : 'Latency'}
              value={summary.avgLatency != null ? `${Math.round(summary.avgLatency)}ms` : 'n/a'}
              detail={summary.avgLatency != null
                ? `${summary.latencyDefinition} · p90 ${
                  summary.p90Latency != null ? `${Math.round(summary.p90Latency)}ms` : 'n/a'
                }`
                : 'response timing not captured'}
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
              {run.mode !== 'text_callable' ? (
                <ConversationFlow
                  turns={conversation?.turns || []}
                  latencyMarks={conversation?.latency_marks || []}
                  firstByteEvidence={conversation?.mode === 'pipecat_webrtc'}
                />
              ) : null}
            </section>

            <section className="card runs-transcript" aria-label="Transcript">
              <p className="eyebrow">Transcript</p>
              <h2>{conversation?.scenario_title || conversation?.scenario_id || 'Conversation'}</h2>
              {run.mode === 'pipecat_webrtc' ? (
                <LiveRunFeedback
                  conversations={run.conversations || []}
                  apiBase={getApiBase()}
                  voice
                  executionRunId={run.execution_run_id}
                  userId={run.user_id || userId}
                  runStatus={run.status}
                />
              ) : null}
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

function formatRuntimeId(value: string) {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatTesterId(value?: string | null) {
  if (value === 'fixture_replay') return 'Saved Conversation Replay';
  if (value === 'pipecat_tester') return 'Pipecat Voice Tester';
  return 'Scenario Simulator';
}

function formatTargetId(value: string) {
  if (value === 'mock_agent') return 'Built-in Sample Agent';
  if (value === 'offline_acc_fixture') return 'Saved ACC Text Replay';
  if (value === 'voice_fixture') return 'Saved ACC Voice Replay';
  if (value === 'builtin_sample_voice') return 'Built-in Generalist Voice Agent';
  if (value === 'sip_agent') return 'SIP Agent Destination';
  if (value === 'phone_agent') return 'Phone Agent Destination';
  if (value === 'browser_webrtc_agent') return 'Browser WebRTC Agent Destination';
  if (value === 'http_endpoint') return 'HTTP Endpoint';
  if (value === 'openai_codex') return 'Connected OpenAI Agent';
  return formatRuntimeId(value);
}

function runTargetSummary(run: ExecutionRunRecord) {
  const snapshot = run.execution_snapshot;
  const agent = snapshot && typeof snapshot.agent === 'object' && snapshot.agent
    ? snapshot.agent as Record<string, unknown>
    : null;
  const target = typeof agent?.target === 'string' ? agent.target : run.mode;
  const environment = typeof agent?.environment === 'string' ? agent.environment : null;
  const evidenceOnly = target === 'offline_acc_fixture' || target === 'voice_fixture';
  const builtInSample = target === 'mock_agent';
  const builtInReference = target === 'builtin_sample_voice';
  const evidenceLabel = evidenceOnly
    ? 'saved evidence replay'
    : builtInReference
      ? 'current-run local reference evidence'
    : builtInSample
      ? 'built-in sample evidence'
      : 'live target evidence';
  return `${formatTargetId(target)}${environment ? ` · ${environment}` : ''} · ${evidenceLabel}`;
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
        <p>{summary.interruption_count} interruption signals in this conversation (from sample data).</p>
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

  const evidence = responseLatencyEvidence(conversation);
  const measuredMarks = evidence.marks
    .map((mark) => ({ mark, ms: markLatencyMs(mark) }))
    .filter((item): item is { mark: Record<string, unknown>; ms: number } => item.ms != null);
  const latency = latencyStats(measuredMarks.map((item) => item.ms));
  if (conversation.mode === 'pipecat_webrtc' && !evidence.marks.length) {
    return (
      <div className="runs-detail-copy">
        <h2>Target Response Latency</h2>
        <p>
          First-audible-audio timing was not captured for this historical run. Its legacy marks measured a complete
          two-agent exchange, so they are excluded rather than presented as target latency.
        </p>
        <p className="latency-definition">
          New streaming voice runs measure from detected caller speech-end until the first audible target PCM frame.
        </p>
      </div>
    );
  }
  return (
    <div className="runs-detail-copy">
      <h2>{evidence.targetSpecific ? 'Target Response Latency' : 'Latency'}</h2>
      {conversation.mode === 'pipecat_webrtc' ? (
        <p className="latency-definition">
          Felt latency is measured directly from the caller&apos;s estimated acoustic end to the target&apos;s first
          audible byte. It spans EOU/ASR finalization + LLM TTLT + TTS TTFB, including pipeline handoff overhead.
        </p>
      ) : null}
      <p>
        count {latency.count} · avg {fmtMs(latency.avg_ms)} · median {fmtMs(latency.median_ms)} · p90 {fmtMs(latency.p90_ms)} ·
        outliers {latency.outlier_count}
      </p>
      <div className="latency-bars" aria-label="Per-mark latency bars">
        {measuredMarks.map(({ mark, ms }, index) => {
          const max = Math.max(latency.max_ms || 1, 1);
          return (
            <div key={index} className="latency-bar-row">
              <span>{latencyMarkLabel(mark, index)}</span>
              <span className="latency-bar-track">
                <i style={{ width: `${Math.min(100, (ms / max) * 100)}%` }} />
              </span>
              <strong>{fmtMs(ms)}</strong>
              <LatencyBreakdown mark={mark} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

type FlowSegment = {
  turn: ConversationTurn;
  lane: 'caller' | 'agent';
  startMs: number;
  durationMs: number;
  responseLatencyMs?: number;
  responseStartMs?: number;
  responseLabel?: string;
};

function ConversationFlow({
  turns,
  latencyMarks,
  firstByteEvidence,
}: {
  turns: ConversationTurn[];
  latencyMarks: Array<Record<string, unknown>>;
  firstByteEvidence: boolean;
}) {
  if (!turns.length) return null;
  const candidateMarks = firstByteEvidence
    ? latencyMarks.filter(isTargetFirstAudioByteMark)
    : latencyMarks;
  const targetMarks = candidateMarks.filter((mark) => markLatencyMs(mark) != null);
  let cursorMs = 0;
  let agentIndex = 0;
  const segments: FlowSegment[] = turns.map((turn) => {
    const lane = turnLane(turn);
    const durationMs = turnAudioDurationMs(turn);
    const mark = lane === 'agent' ? targetMarks[agentIndex++] : undefined;
    const responseLatencyMs = mark ? markLatencyMs(mark) ?? undefined : undefined;
    const responseStartMs = lane === 'agent' && responseLatencyMs != null ? cursorMs : undefined;
    const responseLabel = mark ? responseMetricLabel(mark) : undefined;
    if (responseLatencyMs != null) cursorMs += responseLatencyMs;
    const segment = {
      turn,
      lane,
      startMs: cursorMs,
      durationMs,
      responseLatencyMs,
      responseStartMs,
      responseLabel,
    };
    cursorMs += durationMs;
    return segment;
  });
  const totalMs = Math.max(cursorMs, 1);
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <section className="conversation-flow" aria-label="Two-agent conversation timeline">
      <div className="conversation-flow-heading">
        <div>
          <p className="eyebrow">Conversation flow</p>
          <h3>Tester and target speech</h3>
        </div>
        <div className="conversation-flow-legend" aria-label="Timeline legend">
          <span data-legend="caller">Tester · Caller</span>
          <span data-legend="agent">Target · Agent</span>
          {targetMarks.length ? <span data-legend="latency">Target response wait</span> : null}
        </div>
      </div>
      <div className="conversation-flow-chart">
        {(['caller', 'agent'] as const).map((lane) => (
          <div className="conversation-flow-row" key={lane}>
            <strong>{lane === 'caller' ? 'Caller' : 'Agent'}</strong>
            <div className="conversation-flow-track" data-track={lane}>
              {segments.filter((segment) => segment.lane === lane).map((segment) => (
                <span
                  className="conversation-speech-segment"
                  key={segment.turn.turn_index}
                  style={{
                    left: `${(segment.startMs / totalMs) * 100}%`,
                    width: `${Math.max(1.5, (segment.durationMs / totalMs) * 100)}%`,
                  }}
                  title={`Turn ${segment.turn.turn_index} · ${fmtDuration(segment.durationMs)} · ${segment.turn.text || ''}`}
                >
                  <i aria-hidden="true" />
                  <b>{segment.turn.turn_index}</b>
                </span>
              ))}
              {lane === 'agent' ? segments.filter((segment) => (
                segment.lane === 'agent'
                && segment.responseLatencyMs != null
                && segment.responseStartMs != null
              )).map((segment) => (
                <span
                  className="conversation-response-gap"
                  key={`latency-${segment.turn.turn_index}`}
                  style={{
                    left: `${((segment.responseStartMs || 0) / totalMs) * 100}%`,
                    width: `${Math.max(1, ((segment.responseLatencyMs || 0) / totalMs) * 100)}%`,
                  }}
                  title={`${segment.responseLabel}: ${fmtMs(segment.responseLatencyMs)}`}
                />
              )) : null}
            </div>
          </div>
        ))}
        <div className="conversation-flow-axis" aria-hidden="true">
          <span />
          <div>
            {ticks.map((tick) => <span key={tick}>{fmtDuration(totalMs * tick)}</span>)}
          </div>
        </div>
      </div>
      <ol className="conversation-flow-turns" aria-label="Conversation turn sequence">
        {segments.map((segment) => (
          <li key={`flow-${segment.turn.turn_index}`} data-speaker={segment.lane}>
            <span>{segment.turn.turn_index}</span>
            <strong>{segment.lane === 'caller' ? 'Caller' : 'Agent'}</strong>
            <p>{segment.turn.text || 'No transcript text.'}</p>
            <small>
              {fmtDuration(segment.durationMs)}
              {segment.responseLatencyMs != null
                ? ` · ${String(segment.responseLabel || 'target response').toLowerCase()} ${fmtMs(segment.responseLatencyMs)}`
                : ''}
            </small>
          </li>
        ))}
      </ol>
    </section>
  );
}

function aggregateRunMetrics(run: ExecutionRunRecord | null) {
  const conversations = run?.conversations || [];
  const interruptionCount = conversations.reduce(
    (sum, item) => sum + Number(item.metrics_summary?.interruption_count || 0),
    0,
  );
  const latencyValues = conversations.flatMap((item) => (
    responseLatencyEvidence(item).marks.map(markLatencyMs)
  )).filter((value): value is number => value != null);
  const targetSpecific = conversations.length > 0 && conversations.every((item) => (
    responseLatencyEvidence(item).targetSpecific
  ));
  const usesSpeechEndMetric = conversations.some((item) => (
    responseLatencyEvidence(item).marks.some(isSpeechEndToFirstAudiblePcmMark)
  ));
  const voiceTargetSpecific = conversations.length > 0 && conversations.every(
    (item) => item.mode === 'pipecat_webrtc',
  );
  const measuredLatency = latencyStats(latencyValues);
  const resolutions = conversations.map((item) => Number(item.metrics_summary?.call_resolution_success || 0));
  const latencyBars = latencyValues
    .slice(0, 12)
    .map((value) => Math.min(100, (value / Math.max(measuredLatency.max_ms || 1, 1)) * 100));
  return {
    interruptionCount,
    avgLatency: measuredLatency.avg_ms,
    p90Latency: measuredLatency.p90_ms,
    targetSpecific,
    usesSpeechEndMetric,
    latencyDefinition: usesSpeechEndMetric
      ? 'speech-end → first audible byte'
      : voiceTargetSpecific
        ? 'first audible byte'
        : targetSpecific
          ? 'target response'
          : 'captured latency',
    resolutionRate: resolutions.length ? resolutions.reduce((a, b) => a + b, 0) / resolutions.length : 0,
    latencyBars,
  };
}

function responseLatencyEvidence(conversation: ConversationRecord) {
  const marks = conversation.latency_marks || [];
  if (conversation.mode === 'pipecat_webrtc') {
    return { marks: marks.filter(isTargetFirstAudioByteMark), targetSpecific: true };
  }
  const targetMarks = marks.filter(isTargetResponseMark);
  if (targetMarks.length) {
    return { marks: targetMarks, targetSpecific: true };
  }
  return {
    marks: marks.length
      ? marks
      : (conversation.timeline || []).map((item) => ({ ...item })),
    targetSpecific: false,
  };
}

function isTargetResponseMark(mark: Record<string, unknown>) {
  if (isTargetFirstAudioByteMark(mark)) return true;
  const participant = String(mark.participant || mark.speaker || '').toLowerCase();
  const direction = String(mark.direction || '').toLowerCase();
  if (['target', 'agent', 'assistant'].includes(participant) || direction === 'target_to_tester') {
    return true;
  }
  const descriptor = [mark.kind, mark.type, mark.label, mark.name]
    .map((value) => String(value || '').toLowerCase())
    .join(' ');
  return /\b(target|agent|assistant)\b.*\b(response|latency)\b/.test(descriptor);
}

function isTargetFirstAudioByteMark(mark: Record<string, unknown>) {
  return mark.kind === 'target_first_audio_byte'
    || mark.kind === 'speech_end_to_first_audible_pcm'
    || mark.response_metric === 'target_time_to_first_audio_byte';
}

function isSpeechEndToFirstAudiblePcmMark(mark: Record<string, unknown>) {
  return mark.kind === 'speech_end_to_first_audible_pcm'
    || mark.response_metric === 'speech_end_to_first_audible_pcm';
}

function responseMetricLabel(mark: Record<string, unknown>) {
  return isTargetFirstAudioByteMark(mark)
    ? 'Target first audible byte'
    : 'Target response';
}

function latencyMarkLabel(mark: Record<string, unknown>, index: number) {
  if (isTargetFirstAudioByteMark(mark)) {
    const turnPair = Number(mark.turn_pair);
    return Number.isFinite(turnPair) && turnPair > 0
      ? `Target first audible byte · exchange ${turnPair}`
      : 'Target first audible byte';
  }
  return String(mark.label || `mark ${index + 1}`);
}

function LatencyBreakdown({ mark }: { mark: Record<string, unknown> }) {
  const stages = mark.stage_metrics;
  if (!stages || typeof stages !== 'object') return null;
  const values = stages as Record<string, unknown>;
  const entries = [
    ['EOU + ASR final', values.asr_finalize_ms],
    ['LLM TTFT', values.llm_ttft_ms],
    ['LLM TTLT', values.llm_total_ms],
    ['TTS TTFB', values.tts_ttfb_ms],
  ].filter((entry): entry is [string, number] => Number.isFinite(Number(entry[1])))
    .map(([label, value]) => [label, Number(value)] as const);
  if (!entries.length) return null;
  return (
    <small className="latency-breakdown">
      {entries.map(([label, value]) => `${label} ${fmtMs(value)}`).join(' · ')}
    </small>
  );
}

function latencyStats(values: number[]) {
  const ordered = values.filter((value) => Number.isFinite(value) && value >= 0).sort((a, b) => a - b);
  if (!ordered.length) {
    return {
      count: 0,
      avg_ms: null,
      median_ms: null,
      p90_ms: null,
      min_ms: null,
      max_ms: null,
      outlier_count: 0,
    };
  }
  const midpoint = Math.floor(ordered.length / 2);
  const medianMs = ordered.length % 2
    ? ordered[midpoint]
    : (ordered[midpoint - 1] + ordered[midpoint]) / 2;
  const p90Index = Math.min(ordered.length - 1, Math.max(0, Math.round((ordered.length - 1) * 0.9)));
  return {
    count: ordered.length,
    avg_ms: ordered.reduce((sum, value) => sum + value, 0) / ordered.length,
    median_ms: medianMs,
    p90_ms: ordered[p90Index],
    min_ms: ordered[0],
    max_ms: ordered[ordered.length - 1],
    outlier_count: ordered.filter((value) => medianMs > 0 && value > medianMs * 1.5).length,
  };
}

function turnLane(turn: ConversationTurn): 'caller' | 'agent' {
  const speaker = String(turn.speaker || '').toLowerCase();
  if (turn.direction === 'target_to_tester' || ['agent', 'assistant', 'target'].includes(speaker)) return 'agent';
  return 'caller';
}

function turnAudioDurationMs(turn: ConversationTurn) {
  const metadata = turn.frame_metadata || {};
  const explicitDuration = Number(metadata.duration_ms);
  if (Number.isFinite(explicitDuration) && explicitDuration > 0) return explicitDuration;
  const bytes = Number(metadata.bytes);
  const sampleRate = Number(metadata.sample_rate);
  const channels = Number(metadata.channels || 1);
  if (bytes > 0 && sampleRate > 0 && channels > 0) {
    return (bytes / (sampleRate * channels * 2)) * 1000;
  }
  const words = String(turn.text || '').trim().split(/\s+/).filter(Boolean).length;
  return Math.min(12_000, Math.max(700, (words / 2.6) * 1000));
}

function markLatencyMs(mark: unknown): number | null {
  if (!mark || typeof mark !== 'object') return null;
  const record = mark as Record<string, unknown>;
  for (const key of ['latency_ms', 'elapsed_ms', 'ms', 'duration_ms']) {
    const value = record[key];
    if (typeof value !== 'number') continue;
    if (Number.isFinite(value) && value >= 0) return value;
  }
  return null;
}

function fmtDuration(value: number) {
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}s`;
}

function fmtMs(value?: number | null) {
  if (value == null || Number.isNaN(value)) return 'n/a';
  return `${Math.round(value)}ms`;
}
