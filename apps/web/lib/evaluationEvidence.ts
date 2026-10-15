type RecordValue = Record<string, unknown>;
const object = (value: unknown): RecordValue => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};
const present = (value: unknown): boolean => {
  if (typeof value === 'string') {
    if (!value.trim()) return false;
    try { return present(JSON.parse(value)); } catch { return true; }
  }
  return Array.isArray(value) ? value.length > 0 : Object.keys(object(value)).length > 0;
};

export type EvidencePresence = 'present' | 'missing' | 'unknown';

/** Describe supplied evidence, never infer execution from a score, claim or export. */
export function evaluationEvidence(value: unknown) {
  const report = object(value), scope = object(report.evaluation_scope), audit = object(report.evidence_audit_summary);
  const intake = object(report.vcon_intake_summary), source = object(report.vcon_export);
  const artifact = object(scope.artifact_presence);
  const presence = (name: string, raw: unknown): EvidencePresence => {
    if (typeof artifact[name] === 'boolean') return artifact[name] ? 'present' : 'missing';
    if (typeof audit[`${name}_present`] === 'boolean') return audit[`${name}_present`] ? 'present' : 'missing';
    if (name === 'transcript' && typeof raw === 'string') return raw.trim() ? 'present' : 'missing';
    if (raw !== undefined && raw !== null) return present(raw) ? 'present' : 'missing';
    return 'unknown';
  };
  const transcript = presence('transcript', report.transcript);
  const trace = presence('action_trace', report.action_trace);
  const state = presence('final_state', report.final_state);
  const structured = trace === 'present' || state === 'present';
  const transcriptOnly = transcript === 'present' && trace === 'missing' && state === 'missing';
  // A generated ietf_vcon_export is not evidence that an original vCon was imported.
  const imported = scope.input_source === 'vcon' || Object.keys(intake).length > 0 || source.source_format === 'vcon';
  const synthetic = scope.synthetic === true || intake.synthetic === true;
  const recordedVerdict = report.verdict ?? report.overall;
  const verdict = typeof recordedVerdict === 'string' ? recordedVerdict.toLowerCase() : '';
  const failed = verdict === 'fail' || verdict === 'failed' || verdict === 'error';
  const reviewRequired = report.semantic_review_required === true || scope.semantic_review_required === true || verdict === 'needs_review';
  const headline = failed ? 'Evaluation reported a failure'
    : synthetic ? 'Synthetic evidence — not a verified real-world outcome'
      : transcriptOnly ? 'Outcome unverified'
        : verdict === 'pass' && !reviewRequired && structured ? 'Checks passed on supplied evidence'
          : reviewRequired ? 'Review required' : 'Verification not established';
  const explanation = transcriptOnly
    ? 'The conversation can be assessed, but no tool/action trace or resulting state was supplied. Spoken confirmations do not prove a backend change.'
    : structured
      ? 'Structured evidence was supplied. A tool request is not a successful result; check returned status and resulting state. Source reports are not independently authenticated.'
      : 'Evidence coverage is incomplete or was not recorded. A high score cannot establish successful execution.';
  const rawScore = report.overall_score ?? report.score;
  const score = typeof rawScore === 'number' && Number.isFinite(rawScore) ? rawScore : null;
  const sourceFormat = scope.input_source ?? source.source_format;
  const sourceNames: Record<string, string> = { call: 'Structured call input', group_call: 'Group call input', groupCall: 'Group call input', conversation: 'Structured conversation input', transcript: 'Transcript input' };
  const sourceLabel = imported ? 'Imported vCon' : typeof sourceFormat === 'string' && sourceNames[sourceFormat] ? sourceNames[sourceFormat] : report.scoring_mode === 'transcript' ? 'Transcript input' : 'Input source not recorded';
  return { transcript, trace, state, structured, transcriptOnly, imported, synthetic, failed, reviewRequired, headline, explanation, score,
    sourceLabel: synthetic ? `Synthetic · ${sourceLabel}` : sourceLabel,
    coverageLabel: transcriptOnly ? 'Transcript only' : structured ? 'Structured evidence supplied' : 'Coverage not established',
    verdictLabel: verdict ? verdict.replaceAll('_', ' ') : 'not recorded',
  };
}

export function reviewEvidenceStatus(value: unknown, pending = false): string {
  if (pending) return 'Review in progress';
  const review = object(value);
  if (review.status === 'ready' && ((typeof review.review_id === 'string' && review.review_id.trim()) || Object.keys(object(review.judge_result)).length > 0)) return 'Review recorded separately';
  if (review.status === 'blocked' || review.status === 'failed' || review.status === 'error') return 'Review unavailable — retry when ready';
  return 'No completed review recorded';
}
