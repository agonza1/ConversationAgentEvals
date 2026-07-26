'use client';

import { useEffect, useMemo, useState } from 'react';

import { ApiAwareLink } from '@/components/ApiAwareLink';
import { LiveRunFeedback } from '@/components/LiveRunFeedback';
import { SiteNav } from '@/components/SiteNav';
import {
  applyLlmJudgeReview,
  ConversationRecord,
  ConversationTurn,
  demoProjectId,
  demoUserId,
  ExecutionRunRecord,
  getApiBase,
  getExecutionRun,
  LlmJudgeResponse,
  requestLlmJudge,
} from '@/lib/execution';

type MetricKey = 'audio_interruption' | 'latency' | 'call_resolution';
type ResolutionState = 'verified' | 'unverified' | 'failed' | 'not_evaluated';

const METRICS: Array<{ id: MetricKey; group: string; label: string }> = [
  { id: 'audio_interruption', group: 'Audio', label: 'Interruption Detection' },
  { id: 'latency', group: 'Audio', label: 'Latency' },
  { id: 'call_resolution', group: 'Other', label: 'Resolution Evidence' },
];

export function RunDetailPage({ executionRunId }: { executionRunId: string }) {
  const [run, setRun] = useState<ExecutionRunRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metric, setMetric] = useState<MetricKey>('call_resolution');
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
            {run.mode === 'pipecat_webrtc' ? (
              <div><dt>Session timeout</dt><dd>{run.duplex_timeout_seconds || 120}s</dd></div>
            ) : null}
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
              title={run.mode === 'pipecat_webrtc'
                ? 'End-to-end target response latency'
                : summary.targetSpecific
                  ? 'Target Response Latency'
                  : 'Latency'}
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
              title="Verified Resolution Rate"
              value={summary.resolutionRate == null ? 'n/a' : `${Math.round(summary.resolutionRate)}%`}
              detail={summary.resolutionDetail}
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
              <MetricDetail
                key={`${conversation?.conversation_id || 'no-conversation'}:${run.updated_at}`}
                metric={metric}
                conversation={conversation}
                run={run}
                userId={userId}
                onRunUpdated={setRun}
              />
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
  run,
  userId,
  onRunUpdated,
}: {
  metric: MetricKey;
  conversation: ConversationRecord | null;
  run: ExecutionRunRecord;
  userId: string;
  onRunUpdated: (run: ExecutionRunRecord) => void;
}) {
  const [judge, setJudge] = useState<LlmJudgeResponse | null>(null);
  const [judgeError, setJudgeError] = useState<string | null>(null);
  const [isJudging, setIsJudging] = useState(false);
  const [showJudgePrompt, setShowJudgePrompt] = useState(false);
  const [reviewToApply, setReviewToApply] = useState<LlmJudgeResponse | null>(null);
  const [isApplyingReview, setIsApplyingReview] = useState(false);
  const [applyReviewError, setApplyReviewError] = useState<string | null>(null);
  const summary = conversation?.metrics_summary;
  const deterministicVerdict = summary?.verdict || conversation?.verdict;
  const conversationIsTerminal = Boolean(
    conversation
    && !['queued', 'running'].includes(run.status)
    && !['queued', 'running'].includes(conversation.status),
  );
  const canJudge = Boolean(
    conversationIsTerminal
    && deterministicVerdict,
  );

  async function onJudge() {
    if (!conversation || !canJudge || isJudging) return;
    setJudgeError(null);
    setJudge(null);
    setIsJudging(true);
    try {
      setJudge(await requestLlmJudge({
        plan: 'free',
        user_id: run.user_id || userId,
        execution_run_id: run.execution_run_id,
        conversation_id: conversation.conversation_id,
      }));
    } catch (err) {
      setJudgeError(err instanceof Error ? err.message : 'Could not request the LLM judge.');
    } finally {
      setIsJudging(false);
    }
  }

  async function onApplyJudgeReview() {
    if (
      !conversation
      || !reviewToApply?.review_id
      || !reviewToApply.judge_result?.proposed_evaluation
      || isApplyingReview
    ) return;
    setApplyReviewError(null);
    setIsApplyingReview(true);
    try {
      const updated = await applyLlmJudgeReview({
        executionRunId: run.execution_run_id,
        conversationId: conversation.conversation_id,
        reviewId: reviewToApply.review_id,
        userId: run.user_id || userId,
      });
      setReviewToApply(null);
      onRunUpdated(updated);
    } catch (err) {
      setApplyReviewError(err instanceof Error ? err.message : 'Could not apply the LLM adjudication.');
    } finally {
      setIsApplyingReview(false);
    }
  }

  if (!conversation) {
    return <p className="scenarios-muted">Select a conversation to inspect metrics.</p>;
  }

  if (metric === 'call_resolution') {
    const evidence = resolutionEvidence(conversation);
    const adjudication = conversation.evaluation_adjudication;
    const appliedProposal = adjudication?.judge_result?.proposed_evaluation;
    return (
      <div className="runs-detail-copy">
        <h2>Resolution Evidence</h2>
        <div
          className={`resolution-status is-${evidence.state}`}
          role="status"
          aria-label="Resolution verification status"
        >
          <span>Resolution status</span>
          <strong>{evidence.label}</strong>
          <p>{evidence.description}</p>
        </div>
        <dl className="resolution-facts" aria-label="Resolution evidence details">
          <div><dt>Evaluation score</dt><dd>{evidence.score}</dd></div>
          <div><dt>Evaluator verdict</dt><dd>{evidence.verdict}</dd></div>
          <div>
            <dt>Evaluation basis</dt>
            <dd>{adjudication ? 'Confirmed LLM adjudication' : 'Automatic rule-based evaluation'}</dd>
          </div>
          <div><dt>Final state</dt><dd>{evidence.finalState}</dd></div>
          <div><dt>Termination</dt><dd>{evidence.termination}</dd></div>
          <div><dt>Action evidence</dt><dd>{evidence.actionEvidence}</dd></div>
          <div><dt>Live tool execution</dt><dd>{evidence.liveToolExecution}</dd></div>
          {evidence.outcome ? <div><dt>Recorded outcome</dt><dd>{evidence.outcome}</dd></div> : null}
          {evidence.error ? <div><dt>Recorded error</dt><dd>{evidence.error}</dd></div> : null}
        </dl>
        {evidence.gaps.length ? (
          <div className="resolution-gaps">
            <h3>{adjudication ? 'Remaining gaps after adjudication' : 'Why resolution is not verified'}</h3>
            <ul>
              {evidence.gaps.map((gap) => <li key={gap}>{gap}</li>)}
            </ul>
          </div>
        ) : null}
        {adjudication && appliedProposal ? (
          <AppliedAdjudication
            adjudication={adjudication}
            originalGaps={automaticResolutionGaps(conversation)}
          />
        ) : null}
        <p className="resolution-note">
          The verified rate counts pass verdicts only. Needs-review outcomes stay unverified; the evaluation
          score is not a resolution percentage.
        </p>
        <section className="resolution-judge" aria-label="LLM judge">
          <div>
            <p className="eyebrow">LLM second opinion</p>
            <h3>Review the deterministic verdict</h3>
            <p>
              The score above is automatic and rule-based. The LLM judge separately reviews the transcript
              and recorded evidence, then explains whether it agrees.
            </p>
          </div>
          <button
            type="button"
            className="secondary-link"
            disabled={isJudging || !canJudge}
            onClick={() => void onJudge()}
          >
            {isJudging
              ? 'Reviewing evidence…'
              : !canJudge
                ? conversationIsTerminal
                  ? 'Unavailable without evaluator verdict'
                  : 'Available after run completes'
                : judge
                  ? 'Run LLM review again'
                  : 'Review with LLM judge'}
          </button>
          {judgeError ? <p className="resolution-judge-error" role="alert">{judgeError}</p> : null}
          {judge ? (
            <JudgeResult
              judge={judge}
              showPrompt={showJudgePrompt}
              onTogglePrompt={() => setShowJudgePrompt((current) => !current)}
              onRequestApply={() => {
                setApplyReviewError(null);
                setReviewToApply(judge);
              }}
            />
          ) : null}
        </section>
        {reviewToApply?.judge_result?.proposed_evaluation ? (
          <div className="resolution-adjudication-backdrop" role="presentation">
            <section
              className="resolution-adjudication-dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="apply-adjudication-title"
            >
              <p className="eyebrow">Confirm evaluation update</p>
              <h3 id="apply-adjudication-title">Apply this LLM adjudication?</h3>
              <p>
                The effective verdict will become{' '}
                <strong>{formatRuntimeId(reviewToApply.judge_result.proposed_evaluation.verdict)}</strong>.
                The automatic verdict, score, findings, and evidence remain preserved in the run history.
              </p>
              <p><b>Proposed summary:</b> {reviewToApply.judge_result.proposed_evaluation.summary}</p>
              {reviewToApply.judge_result.proposed_evaluation.corrected_findings.length ? (
                <div>
                  <p><b>Corrections</b></p>
                  <ul>
                    {reviewToApply.judge_result.proposed_evaluation.corrected_findings.map((finding) => (
                      <li key={finding}>{finding}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {reviewToApply.judge_result.proposed_evaluation.remaining_gaps.length ? (
                <div>
                  <p><b>Remaining gaps</b></p>
                  <ul>
                    {reviewToApply.judge_result.proposed_evaluation.remaining_gaps.map((gap) => (
                      <li key={gap}>{gap}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {applyReviewError ? <p className="resolution-judge-error" role="alert">{applyReviewError}</p> : null}
              <div className="resolution-adjudication-actions">
                <button
                  type="button"
                  className="secondary-link"
                  disabled={isApplyingReview}
                  onClick={() => setReviewToApply(null)}
                >
                  Keep automatic evaluation
                </button>
                <button
                  type="button"
                  className="primary-cta"
                  disabled={isApplyingReview}
                  onClick={() => void onApplyJudgeReview()}
                >
                  {isApplyingReview ? 'Applying…' : 'Apply adjudication'}
                </button>
              </div>
            </section>
          </div>
        ) : null}
      </div>
    );
  }

  if (!summary) {
    return <p className="scenarios-muted">This metric was not reported for the selected conversation.</p>;
  }

  if (metric === 'audio_interruption') {
    return (
      <div className="runs-detail-copy">
        <h2>Interruption Detection</h2>
        <p>{summary.interruption_count} interruption signals in this conversation (from sample data).</p>
      </div>
    );
  }

  const evidence = responseLatencyEvidence(conversation);
  const builtInTarget = run.agent_id === 'generalist-voice-agent'
    || run.provenance?.target_kind === 'builtin_sample_voice';
  const measuredMarks = evidence.marks
    .map((mark) => ({ mark, ms: markLatencyMs(mark) }))
    .filter((item): item is { mark: Record<string, unknown>; ms: number } => item.ms != null);
  const latency = latencyStats(measuredMarks.map((item) => item.ms));
  if (conversation.mode === 'pipecat_webrtc' && !evidence.marks.length) {
    return (
      <div className="runs-detail-copy">
        <h2>End-to-end target response latency</h2>
        <p>
          First-audible-audio timing was not captured for this historical run. Its legacy marks measured a complete
          two-agent exchange, so they are excluded rather than presented as target latency.
        </p>
        <p className="latency-definition">
          Tester speech end → first target audio received at tester.
        </p>
      </div>
    );
  }
  return (
    <div className="runs-detail-copy">
      <h2>
        {conversation.mode === 'pipecat_webrtc'
          ? 'End-to-end target response latency'
          : evidence.targetSpecific
            ? 'Target Response Latency'
            : 'Latency'}
      </h2>
      {conversation.mode === 'pipecat_webrtc' ? (
        <p className="latency-definition">
          <strong>Tester speech end → first target audio received at tester.</strong>{' '}
          This end-to-end measurement includes transport, target processing, and media handoff overhead.
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
              <LatencyBreakdown mark={mark} builtInTarget={builtInTarget} />
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

function JudgeResult({
  judge,
  showPrompt,
  onTogglePrompt,
  onRequestApply,
}: {
  judge: LlmJudgeResponse;
  showPrompt: boolean;
  onTogglePrompt: () => void;
  onRequestApply: () => void;
}) {
  const agrees = judge.judge_result?.agrees;
  const title = judge.status === 'blocked'
    ? 'LLM judge unavailable'
    : agrees === true
      ? 'Agrees with the deterministic verdict'
      : agrees === false
        ? 'Disagrees with the deterministic verdict'
        : 'LLM review complete';
  const remaining = judge.spend_control?.remaining_daily_credits;

  return (
    <div className={`resolution-judge-result is-${judge.status}`} aria-label="LLM judge result" role="status">
      <strong>{title}</strong>
      <p>{judge.message}</p>
      {judge.provider || judge.model || judge.latency_ms != null ? (
        <p className="resolution-judge-meta">
          {[
            judge.provider ? `Provider: ${judge.provider}` : null,
            judge.model ? `Model: ${judge.model}` : null,
            judge.latency_ms != null ? `${judge.latency_ms} ms` : null,
            remaining != null ? `${remaining} daily credits remaining` : null,
          ].filter(Boolean).join(' · ')}
        </p>
      ) : null}
      {judge.judge_result?.rationale ? (
        <p><b>Rationale:</b> {judge.judge_result.rationale}</p>
      ) : null}
      {judge.judge_result?.next_action ? (
        <p><b>Next action:</b> {judge.judge_result.next_action}</p>
      ) : null}
      {judge.judge_result?.proposed_evaluation ? (
        <div className="resolution-judge-proposal">
          <p>
            <b>Proposed evaluation:</b>{' '}
            {formatRuntimeId(judge.judge_result.proposed_evaluation.verdict)}
          </p>
          <p>{judge.judge_result.proposed_evaluation.summary}</p>
          {judge.judge_result.proposed_evaluation.corrected_findings.length ? (
            <>
              <p><b>Corrected findings</b></p>
              <ul>
                {judge.judge_result.proposed_evaluation.corrected_findings.map((finding) => (
                  <li key={finding}>{finding}</li>
                ))}
              </ul>
            </>
          ) : null}
          {judge.judge_result.proposed_evaluation.remaining_gaps.length ? (
            <>
              <p><b>Remaining gaps</b></p>
              <ul>
                {judge.judge_result.proposed_evaluation.remaining_gaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            </>
          ) : null}
          {judge.review_id ? (
            <button type="button" className="primary-cta" onClick={onRequestApply}>
              Apply proposed evaluation
            </button>
          ) : (
            <p>Run this review again to create an auditable proposal that can be applied.</p>
          )}
        </div>
      ) : null}
      {!judge.judge_result?.rationale && judge.judge_output ? (
        <p className="resolution-judge-output">{judge.judge_output}</p>
      ) : null}
      {judge.evidence_citations.length ? (
        <div>
          <p><b>Evidence reviewed</b></p>
          <ul>
            {judge.evidence_citations.map((citation) => <li key={citation}>{citation}</li>)}
          </ul>
        </div>
      ) : null}
      {judge.block_reason === 'provider' ? (
        <p><ApiAwareLink href="/benchmarks">Connect OpenAI in the Full console</ApiAwareLink>, then try again.</p>
      ) : null}
      {judge.prompt_preview ? (
        <div>
          <button type="button" className="resolution-judge-prompt-toggle" onClick={onTogglePrompt}>
            {showPrompt ? 'Hide what the judge saw' : 'What the judge saw'}
          </button>
          {showPrompt ? <pre>{judge.prompt_preview}</pre> : null}
        </div>
      ) : null}
    </div>
  );
}

function AppliedAdjudication({
  adjudication,
  originalGaps,
}: {
  adjudication: NonNullable<ConversationRecord['evaluation_adjudication']>;
  originalGaps: string[];
}) {
  const proposal = adjudication.judge_result?.proposed_evaluation;
  if (!proposal) return null;
  const appliedAt = new Date(adjudication.applied_at);
  const appliedLabel = Number.isNaN(appliedAt.getTime())
    ? adjudication.applied_at
    : appliedAt.toLocaleString();
  return (
    <section className="resolution-applied-adjudication" aria-label="Applied LLM adjudication">
      <p className="eyebrow">Applied LLM adjudication</p>
      <h3>{proposal.summary}</h3>
      <p className="resolution-judge-meta">
        {[
          adjudication.provider ? `Provider: ${adjudication.provider}` : null,
          adjudication.model ? `Model: ${adjudication.model}` : null,
          `Confirmed by ${adjudication.applied_by_user_id}`,
          appliedLabel,
        ].filter(Boolean).join(' · ')}
      </p>
      {proposal.corrected_findings.length ? (
        <div>
          <p><b>Corrections accepted</b></p>
          <ul>
            {proposal.corrected_findings.map((finding) => <li key={finding}>{finding}</li>)}
          </ul>
        </div>
      ) : null}
      <details>
        <summary>Original automatic findings</summary>
        {originalGaps.length ? (
          <ul>
            {originalGaps.map((gap) => <li key={gap}>{gap}</li>)}
          </ul>
        ) : (
          <p>No automatic gaps were recorded.</p>
        )}
        <p>
          Original verdict: {formatRuntimeId(String(adjudication.deterministic_snapshot?.verdict || 'not reported'))}
          {' · '}
          Original score: {
            typeof adjudication.deterministic_snapshot?.score === 'number'
              ? `${Math.round(adjudication.deterministic_snapshot.score)}/100`
              : 'not reported'
          }
        </p>
      </details>
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
  const resolutionCounts: Record<ResolutionState, number> = {
    verified: 0,
    unverified: 0,
    failed: 0,
    not_evaluated: 0,
  };
  for (const conversation of conversations) {
    resolutionCounts[resolutionEvidence(conversation).state] += 1;
  }
  const resolutionDetail = conversations.length
    ? [
        `${resolutionCounts.verified} verified`,
        `${resolutionCounts.unverified} unverified`,
        `${resolutionCounts.failed} failed`,
        `${resolutionCounts.not_evaluated} not evaluated`,
      ].join(' · ')
    : 'no conversations';
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
      ? 'Tester speech end → first target audio received at tester'
      : voiceTargetSpecific
        ? 'first audible byte'
        : targetSpecific
          ? 'target response'
          : 'captured latency',
    resolutionRate: conversations.length
      ? (resolutionCounts.verified / conversations.length) * 100
      : null,
    resolutionDetail,
    latencyBars,
  };
}

function resolutionEvidence(conversation: ConversationRecord) {
  const summary = conversation.metrics_summary;
  const proposed = conversation.evaluation_adjudication?.judge_result?.proposed_evaluation;
  const rawVerdict = String(
    proposed?.verdict || summary?.verdict || conversation.verdict || '',
  ).trim().toLowerCase();
  const state: ResolutionState = rawVerdict === 'pass' || (!rawVerdict && summary?.call_resolution_success === 100)
    ? 'verified'
    : rawVerdict === 'fail' || rawVerdict === 'failed' || conversation.status === 'failed'
      ? 'failed'
      : rawVerdict === 'needs_review'
        ? 'unverified'
        : 'not_evaluated';
  const labels: Record<ResolutionState, string> = {
    verified: 'Verified',
    unverified: 'Unverified',
    failed: 'Failed',
    not_evaluated: 'Not evaluated',
  };
  const descriptions: Record<ResolutionState, string> = {
    verified: 'The evaluator returned a pass verdict and counted this conversation as a verified resolution.',
    unverified: 'The evaluator found useful evidence but could not prove the required outcome.',
    failed: 'The run failed or the evaluator returned a failure verdict.',
    not_evaluated: 'No resolution verdict is available for this conversation.',
  };
  if (proposed) {
    descriptions[state] = `A user confirmed the LLM adjudication: ${proposed.summary}`;
  }
  const finalState = conversation.final_state || {};
  const runtime = asRecord(finalState.runtime_provenance);
  const finalComplete = typeof finalState.complete === 'boolean' ? finalState.complete : null;
  const terminationReason = stringValue(finalState.termination_reason)
    || stringValue(finalState.tester_termination_reason);
  const outcome = stringValue(finalState.outcome);
  const error = stringValue(conversation.error);
  const actionCount = conversation.action_trace?.length || 0;
  const evaluationScore = summary?.score ?? conversation.score;
  const liveToolExecution = typeof runtime.live_tool_execution === 'boolean'
    ? runtime.live_tool_execution
    : null;
  const gaps = proposed
    ? proposed.remaining_gaps.map(asSentence)
    : automaticResolutionGaps(conversation);
  if (proposed && (state === 'unverified' || state === 'failed')) {
    if (finalComplete === false) gaps.push('The recorded final state was not complete.');
    if (actionCount === 0) gaps.push('No action or tool evidence was recorded.');
    if (liveToolExecution === false) gaps.push('The target did not execute a live business tool.');
    if (terminationReason === 'max_exchanges') gaps.push('The conversation reached the configured exchange limit.');
    if (error) gaps.push('The run recorded an execution error.');
    if (!gaps.length) gaps.push('The adjudicated verdict is not a pass, so resolution remains unverified.');
  }

  return {
    state,
    label: labels[state],
    description: descriptions[state],
    score: typeof evaluationScore === 'number'
      ? `${Math.round(evaluationScore)}/100`
      : 'Not reported',
    verdict: rawVerdict ? formatRuntimeId(rawVerdict) : 'Not reported',
    finalState: finalComplete === true ? 'Complete' : finalComplete === false ? 'Not complete' : 'Not reported',
    termination: terminationReason ? formatRuntimeId(terminationReason) : 'Not reported',
    actionEvidence: actionCount ? `${actionCount} recorded` : 'None recorded',
    liveToolExecution: liveToolExecution === true ? 'Yes' : liveToolExecution === false ? 'No' : 'Not reported',
    outcome: outcome ? formatRuntimeId(outcome) : null,
    error,
    gaps: [...new Set(gaps)],
  };
}

function automaticResolutionGaps(conversation: ConversationRecord) {
  const summary = conversation.metrics_summary;
  const rawVerdict = String(summary?.verdict || conversation.verdict || '').trim().toLowerCase();
  const state: ResolutionState = rawVerdict === 'pass'
    ? 'verified'
    : rawVerdict === 'fail' || rawVerdict === 'failed' || conversation.status === 'failed'
      ? 'failed'
      : rawVerdict === 'needs_review'
        ? 'unverified'
        : 'not_evaluated';
  if (state !== 'unverified' && state !== 'failed') return [];

  const finalState = conversation.final_state || {};
  const runtime = asRecord(finalState.runtime_provenance);
  const finalComplete = typeof finalState.complete === 'boolean' ? finalState.complete : null;
  const terminationReason = stringValue(finalState.termination_reason)
    || stringValue(finalState.tester_termination_reason);
  const liveToolExecution = typeof runtime.live_tool_execution === 'boolean'
    ? runtime.live_tool_execution
    : null;
  const gaps: string[] = [];
  const findings = asRecord(conversation.evaluation_findings);
  for (const finding of findingLabels(findings.missing_actions)) {
    gaps.push(`Missing required action: ${finding}.`);
  }
  for (const finding of failedRubricLabels(findings.rubric_checks)) {
    gaps.push(`Failed rubric check: ${finding}.`);
  }
  for (const finding of findingLabels(findings.hard_check_failures)) {
    gaps.push(`Hard-check failure: ${finding}.`);
  }
  if (finalComplete === false) gaps.push('The recorded final state was not complete.');
  if (!conversation.action_trace?.length) gaps.push('No action or tool evidence was recorded.');
  if (liveToolExecution === false) gaps.push('The target did not execute a live business tool.');
  if (terminationReason === 'max_exchanges') gaps.push('The conversation reached the configured exchange limit.');
  if (conversation.error) gaps.push('The run recorded an execution error.');
  if (!gaps.length) gaps.push('The evaluator did not return the pass verdict required for verified resolution.');
  return gaps;
}

function asSentence(value: string) {
  const trimmed = value.trim();
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

function findingLabels(value: unknown, limit = 6) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => findingLabel(item))
    .filter((item): item is string => Boolean(item))
    .slice(0, limit);
}

function failedRubricLabels(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item) => {
      const finding = asRecord(item);
      const status = stringValue(finding.status)?.toLowerCase();
      return finding.passed === false || ['fail', 'failed', 'missing'].includes(status || '');
    })
    .map((item) => findingLabel(item))
    .filter((item): item is string => Boolean(item))
    .slice(0, 6);
}

function findingLabel(value: unknown) {
  if (typeof value === 'string') return formatRuntimeId(value);
  const finding = asRecord(value);
  const detail = (
    stringValue(finding.summary)
    || stringValue(finding.description)
    || stringValue(finding.message)
  );
  const identifier = (
    stringValue(finding.label)
    || stringValue(finding.name)
    || stringValue(finding.id)
    || stringValue(finding.category)
  );
  const structuredDetail = hardCheckDetail(finding);
  if (identifier && detail && identifier.toLowerCase() !== detail.toLowerCase()) {
    return `${formatRuntimeId(identifier)} — ${detail}`;
  }
  if (identifier && structuredDetail) {
    return `${formatRuntimeId(identifier)} — ${structuredDetail}`;
  }
  return detail || structuredDetail || (identifier ? formatRuntimeId(identifier) : null);
}

function hardCheckDetail(finding: Record<string, unknown>) {
  const action = stringValue(finding.action);
  const expectedAfter = stringValue(finding.expected_after);
  if (action && expectedAfter) {
    return `${formatRuntimeId(action)} was observed before ${formatRuntimeId(expectedAfter)}`;
  }
  if (action) return `Action: ${formatRuntimeId(action)}`;

  const path = stringValue(finding.path);
  const hasExpected = Object.prototype.hasOwnProperty.call(finding, 'expected');
  const hasActual = Object.prototype.hasOwnProperty.call(finding, 'actual');
  if (path && (hasExpected || hasActual)) {
    const expected = hasExpected ? findingValue(finding.expected) : 'not specified';
    const actual = hasActual ? findingValue(finding.actual) : 'not reported';
    return `${formatRuntimeId(path)} expected ${expected}, got ${actual}`;
  }
  return null;
}

function findingValue(value: unknown) {
  if (typeof value === 'string') return `"${value}"`;
  const encoded = JSON.stringify(value);
  return encoded === undefined ? String(value) : encoded;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringValue(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
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
  return isEndToEndTargetResponseMark(mark)
    || mark.kind === 'target_first_audio_byte'
    || mark.kind === 'speech_end_to_first_audible_byte'
    || mark.kind === 'speech_end_to_first_audible_pcm'
    || mark.response_metric === 'speech_end_to_first_audible_byte'
    || mark.response_metric === 'target_time_to_first_audio_byte';
}

function isEndToEndTargetResponseMark(mark: Record<string, unknown>) {
  return mark.kind === 'tester_speech_end_to_first_target_audio_received'
    || mark.response_metric === 'tester_speech_end_to_first_target_audio_received';
}

function isSpeechEndToFirstAudiblePcmMark(mark: Record<string, unknown>) {
  return isEndToEndTargetResponseMark(mark)
    || mark.kind === 'speech_end_to_first_audible_byte'
    || mark.kind === 'speech_end_to_first_audible_pcm'
    || mark.response_metric === 'speech_end_to_first_audible_byte'
    || mark.response_metric === 'speech_end_to_first_audible_pcm';
}

function responseMetricLabel(mark: Record<string, unknown>) {
  if (isEndToEndTargetResponseMark(mark)) {
    return 'End-to-end target response';
  }
  return isTargetFirstAudioByteMark(mark)
    ? 'Target first audible byte'
    : 'Target response';
}

function latencyMarkLabel(mark: Record<string, unknown>, index: number) {
  if (isEndToEndTargetResponseMark(mark)) {
    const turnPair = Number(mark.turn_pair);
    return Number.isFinite(turnPair) && turnPair > 0
      ? `End-to-end target response · exchange ${turnPair}`
      : 'End-to-end target response';
  }
  if (isTargetFirstAudioByteMark(mark)) {
    const turnPair = Number(mark.turn_pair);
    return Number.isFinite(turnPair) && turnPair > 0
      ? `Target first audible byte · exchange ${turnPair}`
      : 'Target first audible byte';
  }
  return String(mark.label || `mark ${index + 1}`);
}

function LatencyBreakdown({
  mark,
  builtInTarget,
}: {
  mark: Record<string, unknown>;
  builtInTarget: boolean;
}) {
  const stages = mark.stage_metrics;
  if (!stages || typeof stages !== 'object') return null;
  const values = stages as Record<string, unknown>;
  const entries = [
    ['Target endpointing + ASR finalization', values.asr_finalize_ms],
    ['LLM TTFT', values.llm_ttft_ms],
    ['TTS text aggregation', values.tts_aggregation_delay_ms],
    [
      'TTS synthesis TTFB',
      values.tts_synthesis_ttfb_ms ?? values.tts_ttfb_ms,
    ],
  ].filter((entry): entry is [string, number] => (
    typeof entry[1] === 'number' && Number.isFinite(entry[1])
  ));
  if (!entries.length) return null;
  const source = String(
    mark.stage_metrics_source || (builtInTarget ? 'built_in_target' : 'target_provided'),
  );
  const heading = source === 'built_in_target'
    ? 'Built-in target diagnostics'
    : 'Target-provided diagnostics';
  return (
    <div className="latency-breakdown">
      <strong>{heading}</strong>
      <small>{entries.map(([label, value]) => `${label} ${fmtMs(value)}`).join(' · ')}</small>
    </div>
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
