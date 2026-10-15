import type { JudgeProposedEvaluation } from './execution';

type ObjectValue = Record<string, unknown>;
const object = (value: unknown): ObjectValue => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as ObjectValue : {};
const text = (value: unknown): string | null => typeof value === 'string' && value.trim() ? value : null;
const scalar = (value: unknown): string | number | boolean | null => typeof value === 'string' || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value)) ? value : null;
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const identifier = (value: unknown): string | null => typeof value === 'string' && /^[a-zA-Z0-9_.:/-]{1,160}$/.test(value) && !value.includes('://') && !value.startsWith('/') && !/^[A-Za-z]:[\\/]/.test(value) && !/(^|\/)\.{1,2}(\/|$)/.test(value) && !/(^|\/)(artifacts|storage|apps|node_modules|\.codex)(\/|$)/i.test(value) && !/(client[_-]?secret|secret[_-]?access[_-]?key|secret|password|token|private[_-]?key|api[_-]?key)[:=]/i.test(value) && !/\/(Users|home|workspace|private|tmp|opt|app|root|var|mnt|Volumes|etc|srv)\//.test(value) ? value : null;
const hash = (value: unknown, length: number): string | null => typeof value === 'string' && new RegExp(`^[a-f0-9]{${length}}$`).test(value) ? value : null;

export interface DimensionView {
  name: string; value: string | number | boolean | null; outcome: string;
  justification: string | null; ordinalLabel: string | null; hasScale: boolean;
  rubric: Array<{ value: string | number; label: string }>;
}
export interface BehaviorView {
  name: string; outcome: string; relevant: boolean | null; violated: boolean | null;
  confidence: string | number | boolean | null; reasoning: string | null; references: string[];
}
export interface EvidenceMessageView {
  id: string; role: string; text: string; speakerLabel: string;
}
export interface EvidenceReferencePartView {
  quote: string; status: 'matched' | 'ambiguous' | 'unresolved';
  messageId: string | null; detail: string | null;
}
export interface EvidenceReferenceView {
  index: string | number; description: string; parts: EvidenceReferencePartView[];
}
export interface AssertReviewView {
  id: string; status: string; statusLabel: string; dateLabel: string; timestamp: number | null;
  model: string | null; version: string | null; evidenceLabel: string; rationale: string | null;
  dimensions: DimensionView[]; behaviors: BehaviorView[]; citations: string[];
  technical: Array<{ label: string; value: string | null }>;
  proposal: JudgeProposedEvaluation | null;
  evidenceMessages: EvidenceMessageView[];
  evidenceReferences: EvidenceReferenceView[];
  coverage: { transcript: boolean | null; toolTrace: boolean | null; finalState: boolean | null };
  adapterVersion: string | null; legacyEvidence: boolean;
  agrees: boolean | null; nextAction: string | null; latencyMs: number | null;
  evidenceSnapshotAvailable: boolean;
  evidenceDetailsStatus: 'complete' | 'limited' | 'legacy' | 'invalid';
  evidenceDetailsMessage: string | null;
  automaticSnapshot: { verdict: string | null; score: number | null } | null;
}

function automaticSnapshot(value: unknown): AssertReviewView['automaticSnapshot'] {
  const snapshot = object(value);
  const verdict = snapshot.verdict === 'pass' || snapshot.verdict === 'needs_review'
    || snapshot.verdict === 'fail' || snapshot.verdict === 'failed' ? snapshot.verdict : null;
  const score = typeof snapshot.score === 'number' && Number.isFinite(snapshot.score) ? snapshot.score : null;
  return verdict !== null || score !== null ? { verdict, score } : null;
}

const messageId = (value: unknown): string | null => {
  if (typeof value !== 'string' || !/^event:(0|[1-9]\d*)$/.test(value)) return null;
  return Number.isSafeInteger(Number(value.slice(6))) ? value : null;
};

function evidenceMessages(value: unknown): EvidenceMessageView[] {
  if (!Array.isArray(value)) return [];
  const rows = value.map(object);
  const counts = new Map<string, number>();
  for (const row of rows) {
    const id = messageId(row.id);
    if (id !== null) counts.set(id, (counts.get(id) || 0) + 1);
  }
  return rows.flatMap((row) => {
    const id = messageId(row.id), body = text(row.text);
    // A repeated or malformed ID cannot safely become a transcript anchor.
    if (id === null || counts.get(id) !== 1 || body === null) return [];
    const role = row.role === 'user' || row.role === 'assistant' || row.role === 'system'
      ? row.role : 'unknown';
    return [{ id, role, text: body, speakerLabel: role === 'user' ? 'Caller'
      : role === 'assistant' ? 'Agent' : role === 'system' ? 'System' : 'Unknown speaker' }];
  });
}

function quoteOccurrences(body: string, quote: string): number {
  let count = 0, offset = 0;
  while (offset <= body.length) {
    const found = body.indexOf(quote, offset);
    if (found < 0) break;
    count += 1;
    if (count > 1) break;
    offset = found + 1;
  }
  return count;
}

function normalizeReferencePart(value: unknown, messages: EvidenceMessageView[], snapshotAvailable: boolean): EvidenceReferencePartView {
  const part = object(value), quote = text(part.quote) || '';
  const declared = part.status === 'matched' || part.status === 'ambiguous' ? part.status : 'unresolved';
  const id = messageId(part.message_id);
  const detail = text(part.detail);
  if (declared !== 'matched') return { quote, status: declared, messageId: null, detail };
  if (!snapshotAvailable) return { quote, status: 'unresolved', messageId: null,
    detail: 'A complete, valid judge-message snapshot is unavailable for this reference.' };
  if (!quote || id === null) return { quote, status: 'unresolved', messageId: null,
    detail: 'The recorded reference has no safe message ID and exact quote.' };
  const target = messages.find((message) => message.id === id);
  if (!target) return { quote, status: 'unresolved', messageId: null,
    detail: 'The referenced message is unavailable in this saved judge snapshot.' };
  const matches = messages.reduce((total, message) => total + quoteOccurrences(message.text, quote), 0);
  if (matches > 1) return { quote, status: 'ambiguous', messageId: null,
    detail: 'This quote occurs more than once in the saved judge snapshot.' };
  if (matches !== 1 || !target.text.includes(quote)) return { quote, status: 'unresolved', messageId: null,
    detail: 'The quote does not match the referenced message in the saved judge snapshot.' };
  return { quote, status: 'matched', messageId: id, detail };
}

// Older reviews stored JSON excerpts, sometimes deliberately truncated mid-field.
// Read only complete scalar fields; never parse a truncated object or derive a link.
function recordedStringField(value: string, key: string): string | null {
  const match = new RegExp(`"${key}"\\s*:\\s*("(?:\\\\.|[^"\\\\])*")`).exec(value);
  if (!match) return null;
  try { return text(JSON.parse(match[1])); } catch { return null; }
}

function legacyReference(value: string, index: number): EvidenceReferenceView {
  const jsonLike = value.trim().startsWith('{') || value.trim().startsWith('[');
  const description = recordedStringField(value, 'description');
  const quote = recordedStringField(value, 'quoted_text') || recordedStringField(value, 'exact');
  return { index: index + 1, description: description || (jsonLike ? 'Historical evidence reference' : value),
    parts: [{ quote: quote || (jsonLike ? '' : value), status: 'unresolved', messageId: null,
      detail: 'Historical reference: no verified transcript anchor was retained with this review.' }] };
}

function normalizeReview(value: unknown, allowLive: boolean): AssertReviewView | null {
  const review = object(value); const result = object(review.judge_result); const provenance = object(result.provenance);
  if (provenance.engine !== 'assert' && review.provider !== 'assert-ai') return null;
  const live = allowLive && review.status === 'ready';
  const id = text(review.review_id);
  if (id === null && !live) return null;
  const dimensions = object(provenance.dimensions), applicability = object(provenance.dimension_applicability);
  const justification = object(provenance.dimension_justifications), scales = object(provenance.dimension_scales);
  const names = [...new Set([...Object.keys(dimensions), ...Object.keys(applicability), ...Object.keys(justification), ...Object.keys(scales)])];
  const dimensionViews = names.map((name): DimensionView => {
    const value = scalar(dimensions[name]); const scale = object(scales[name]);
    const rubric = scale.type === 'ordinal' && Array.isArray(scale.values) ? scale.values.flatMap((entry) => {
      const item = object(entry);
      return (typeof item.value === 'string' || (typeof item.value === 'number' && Number.isFinite(item.value))) && typeof item.label === 'string'
        ? [{ value: item.value, label: item.label }] : [];
    }) : [];
    const match = rubric.filter((entry) => typeof entry.value === typeof value && entry.value === value);
    return { name, value, outcome: applicability[name] === false ? 'Not applicable'
      : value === true ? 'Flagged' : value === false ? 'Clear' : value === null ? 'Unavailable' : 'Recorded value',
      justification: text(justification[name]), ordinalLabel: match.length === 1 ? match[0].label : null,
      hasScale: scale.type === 'ordinal', rubric };
  });
  const nodes = Array.isArray(provenance.node_judgments) ? provenance.node_judgments : [];
  const behaviors = nodes.map((raw): BehaviorView => {
    const node = object(raw); const relevant = typeof node.relevant === 'boolean' ? node.relevant : null;
    const violated = typeof node.violated === 'boolean' ? node.violated : null;
    return { name: text(node.node_name) || 'Behavior name unavailable', relevant, violated,
      outcome: relevant === false ? 'Not relevant' : relevant === true && violated === true ? 'Flagged'
        : relevant === true && violated === false ? 'Clear' : 'Unavailable',
      confidence: scalar(node.confidence), reasoning: text(node.reasoning), references: [
        ...(Array.isArray(node.evidence_turns) ? node.evidence_turns.map((turn) =>
          typeof turn === 'number' && Number.isInteger(turn) && turn >= 0 ? `Judge turn ${turn}`
            : `Unsupported judge-turn reference: ${scalar(turn) === null ? 'Unavailable' : String(scalar(turn))}`)
          : node.evidence_turns === undefined ? [] : ['Judge-turn references unavailable (malformed field)']),
        ...strings(node.evidence_references),
      ] };
  }).map((node, index) => ({ node, index, rank: node.outcome === 'Flagged' ? 0 : node.outcome === 'Clear' ? 1 : 2 }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index).map((entry) => entry.node);
  const parsedDate = typeof review.created_at === 'string' && review.created_at.trim() ? Date.parse(review.created_at) : NaN;
  const timestamp = Number.isFinite(parsedDate) ? parsedDate : null;
  const proposal = object(result.proposed_evaluation);
  const validProposal: JudgeProposedEvaluation | null = (proposal.verdict === 'pass' || proposal.verdict === 'fail' || proposal.verdict === 'needs_review') && typeof proposal.summary === 'string'
    ? { verdict: proposal.verdict, summary: proposal.summary, corrected_findings: strings(proposal.corrected_findings), remaining_gaps: strings(proposal.remaining_gaps) } : null;
  const version = typeof provenance.assert_version === 'string' && /^\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?$/.test(provenance.assert_version) ? provenance.assert_version : null;
  const model = identifier(review.model);
  const status = live ? (id === null ? 'completed' : 'pending_confirmation') : typeof review.status === 'string' ? review.status : 'unknown';
  const messages = evidenceMessages(provenance.evidence_messages);
  const citations = strings(review.evidence_citations);
  const limited = provenance.evidence_details_status === 'limited';
  const adapterVersion = identifier(provenance.transcript_adapter_version);
  const snapshotAvailable = adapterVersion !== null && !limited && Array.isArray(provenance.evidence_messages)
    && messages.length === provenance.evidence_messages.length && messages.every((message) => message.role !== 'unknown');
  const references = Array.isArray(provenance.evidence_references) ? provenance.evidence_references.map((raw, index): EvidenceReferenceView => {
    const reference = object(raw);
    return { index: typeof reference.index === 'string' && reference.index.trim() ? reference.index
      : typeof reference.index === 'number' && Number.isFinite(reference.index) ? reference.index : index + 1,
      description: text(reference.description) || 'Evidence reference',
      parts: Array.isArray(reference.parts) ? reference.parts.map((part) => normalizeReferencePart(part, messages, snapshotAvailable)) : [] };
  }) : citations.map(legacyReference);
  const coverage = object(provenance.evidence_coverage);
  const legacyEvidence = !limited && (adapterVersion === null || !Array.isArray(provenance.evidence_messages)
    || !Array.isArray(provenance.evidence_references));
  return { id: id || 'live-assert-review', status,
    statusLabel: status === 'pending_confirmation' ? 'Pending confirmation' : status === 'applied' ? 'Applied' : status === 'superseded' ? 'Superseded' : status === 'completed' ? 'Review complete' : 'Status unknown',
    timestamp, dateLabel: timestamp === null ? 'Recorded time unavailable' : new Date(timestamp).toLocaleString(),
    model, version, evidenceLabel: provenance.evidence_level === 'gray_box' ? 'Trace-backed evidence' : provenance.evidence_level === 'partial_structured' ? 'Partially structured evidence' : provenance.evidence_level === 'black_box' ? 'Transcript-only evidence' : 'Evidence level unavailable',
    rationale: text(result.rationale), proposal: validProposal, dimensions: dimensionViews, behaviors, citations,
    evidenceMessages: messages, evidenceReferences: references,
    coverage: { transcript: typeof coverage.transcript === 'boolean' ? coverage.transcript : null,
      toolTrace: typeof coverage.tool_trace === 'boolean' ? coverage.tool_trace : null,
      finalState: typeof coverage.final_state === 'boolean' ? coverage.final_state : null },
    adapterVersion, legacyEvidence,
    evidenceSnapshotAvailable: snapshotAvailable,
    evidenceDetailsStatus: limited ? 'limited' : legacyEvidence ? 'legacy' : snapshotAvailable ? 'complete' : 'invalid',
    evidenceDetailsMessage: text(provenance.evidence_details_message),
    automaticSnapshot: automaticSnapshot(review.deterministic_snapshot),
    agrees: typeof result.agrees === 'boolean' ? result.agrees : null,
    nextAction: text(result.next_action),
    latencyMs: typeof review.latency_ms === 'number' && Number.isFinite(review.latency_ms) && review.latency_ms >= 0 ? review.latency_ms : null,
    technical: [{ label: 'Input fingerprint', value: hash(provenance.input_fingerprint, 16) },
      { label: 'Score SHA-256', value: hash(provenance.score_sha256, 64) },
      { label: 'Output SHA-256', value: hash(review.output_sha256, 64) },
      { label: 'ASSERT version', value: version }, { label: 'Judge model', value: model },
      { label: 'Transcript adapter', value: adapterVersion }],
  };
}

export function normalizeAssertReview(value: unknown): AssertReviewView | null {
  return normalizeReview(value, false);
}

/** A consistent evidence view for both an immediate result and a saved assessment. */
export function buildAssertReportView(value: unknown): AssertReviewView | null {
  return normalizeReview(value, true);
}

export function sortedAssertReviews(values: unknown): AssertReviewView[] {
  if (!Array.isArray(values)) return [];
  return values.map(normalizeAssertReview).filter((value): value is AssertReviewView => value !== null)
    .map((review, index) => ({ review, index })).sort((a, b) =>
      (b.review.timestamp ?? -Infinity) - (a.review.timestamp ?? -Infinity) || b.index - a.index)
    .map((entry) => entry.review);
}
